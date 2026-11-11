"""Orchestrator: wires the specialist agents into one LangGraph pipeline.

Unlike the earlier version of this module, this one drives the real
manual-adapter workflow instead of assuming pre-parsed extracts already
exist: zoning, litigation, land_records, and owner_background each call
their adapter's instructions() and then pause with LangGraph's interrupt()
until a human hands back an uploaded file, at which point parse() and the
agent's report() run for real. This is the actual feature CLAUDE.md named
LangGraph for ("orchestration, shared state, checkpointing, human-in-the-
loop") -- the earlier version's "just pass in already-parsed extracts"
was a stand-in for this, not the real design.

Verified empirically against the installed langgraph version before
writing this: when several nodes interrupt in the same superstep (as
happens here -- zoning, litigation, land_records, and owner_background
all pause in parallel),
graph.invoke() returns a state dict whose "__interrupt__" key lists every
pending Interrupt(value=..., id=...). Resuming is Command(resume={id:
value, ...}); interrupts can be resolved one at a time or together,
partial resume works, and each unresolved interrupt just stays pending.

growth_potential reuses the same screenshot zoning already fetched (see
schemas/zoning.py docstring) rather than triggering its own interrupt, so
it runs sequentially after zoning instead of in parallel with it -- see
_build_graph for the edge shape this produces.

document_analysis needs no interrupt: its PDFs are supplied up front
(Phase 1's "works offline on uploaded PDFs" design), not fetched
interactively mid-run.
"""

import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from bhoomicheck.adapters.kuda import KudaMasterPlanAdapter
from bhoomicheck.adapters.land_records import LandRecordsAdapter
from bhoomicheck.adapters.litigation import LitigationAdapter
from bhoomicheck.adapters.owner_background import OwnerBackgroundAdapter
from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.agents.growth_potential import GrowthPotentialAgent
from bhoomicheck.agents.land_records import LandRecordsAgent
from bhoomicheck.agents.litigation import LitigationAgent
from bhoomicheck.agents.owner_background import OwnerBackgroundAgent
from bhoomicheck.agents.risk_report import RiskReportAgent
from bhoomicheck.agents.zoning import ZoningAgent
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.document import DocumentAnalysisReport
from bhoomicheck.schemas.growth_potential import GrowthPotentialReport
from bhoomicheck.schemas.land_records import LandRecordsReport
from bhoomicheck.schemas.litigation import LitigationReport, LitigationSearchQuery
from bhoomicheck.schemas.owner_background import OwnerBackgroundReport, OwnerBackgroundQuery
from bhoomicheck.geo.road_network import RoadNetwork, load_default_network
from bhoomicheck.schemas.geo import LatLon
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.risk_report import RiskReport
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract, ZoningReport

def _checkpoint_allowed_types() -> list[type[BaseModel]]:
    """Every Pydantic model under bhoomicheck.schemas, for the checkpointer.

    Derived by walking the package rather than hand-listed, and that is
    the whole point. LangGraph's serializer treats an explicit allowlist
    as a *block*list for everything else -- and a blocked type does not
    raise, it silently comes back as a plain dict. A hand-written list
    therefore fails in the nastiest possible way: add a schema, forget to
    register it, and some agent downstream gets a dict instead of a model
    and dies with an unrelated-looking AttributeError.

    That is not hypothetical -- it is exactly what happened the first
    time this ran against a real SQLite checkpointer: ParcelIdentifier,
    the two query models, and KudaMasterPlanExtract were all missing from
    a hand-written list, and growth_potential crashed on a dict. The
    in-memory checkpointer never caught it because it does not round-trip
    state through serialization at all.
    """
    import importlib
    import pkgutil

    from bhoomicheck import schemas

    found: dict[str, type[BaseModel]] = {}
    for module_info in pkgutil.iter_modules(schemas.__path__):
        module = importlib.import_module(f"{schemas.__name__}.{module_info.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                # Keyed by module+name so a model imported into several
                # schema modules is only registered once.
                found[f"{obj.__module__}.{obj.__name__}"] = obj
    return list(found.values())


def _serializer() -> JsonPlusSerializer:
    """The one serializer config both checkpointer flavors share."""
    return JsonPlusSerializer(allowed_msgpack_modules=_checkpoint_allowed_types())


def _default_checkpointer() -> InMemorySaver:
    """InMemorySaver with our report types allowlisted for serialization.

    Fine for tests and one-shot runs; state dies with the process. For a
    real check spread over days, use sqlite_checkpointer instead.
    """
    return InMemorySaver(serde=_serializer())


def sqlite_checkpointer(db_path: Path) -> SqliteSaver:
    """A checkpointer that survives the process exiting.

    This is what makes the manual-adapter workflow actually work as
    designed: a real check means fetching a KUDA screenshot today, a Bhu
    Bharati one tomorrow, and sitting through eCourts captchas next week.
    With InMemorySaver, closing the terminal loses the whole run. Backed
    by a plain SQLite file, so there's no database server to run -- the
    "deployment" is a file on disk.

    check_same_thread=False is deliberate and safe here: LangGraph runs
    our parallel specialist nodes on a thread pool, so the connection is
    touched from more than one thread, and SqliteSaver's own docs state
    it guards access with an internal lock.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn, serde=_serializer())


class OrchestratorState(TypedDict):
    """Shared state every node reads from and writes back into.

    A TypedDict, not a Pydantic model: LangGraph's own convention for
    graph state, separate from the Pydantic validation each agent still
    does on its own output.

    Attributes:
        parcel: The parcel being checked -- shared by every node, and the
            query zoning/land_records search by directly.
        pdf_paths: Uploaded documents for DocumentAnalysisAgent, supplied
            up front (no interrupt -- see module docstring).
        location: The parcel's coordinate, when one is known. Optional
            because the revenue identity (survey number, village) is
            what identifies a parcel and a coordinate is an extra the
            caller may not have. Supplying it is what lets zoning_node
            measure the master-plan setback instead of reading it off a
            screenshot.
        litigation_query: Who to search eCourts for.
        owner_background_query: Who to search IGRS for.
        document_report / zoning_report / litigation_report /
            land_records_report / owner_background_report /
            growth_potential_report: Filled in once that node completes;
            None until then.
        zoning_extracts: The (upload_path, extract) pairs zoning_node
            fetched -- growth_potential_node reads this instead of
            triggering its own interrupt (same screenshot, see
            schemas/zoning.py docstring). None until zoning_node runs.
        final_report: Filled in by risk_report once every specialist
            report exists.
    """

    parcel: ParcelIdentifier
    pdf_paths: list[Path]
    location: LatLon | None
    litigation_query: LitigationSearchQuery
    owner_background_query: OwnerBackgroundQuery
    document_report: DocumentAnalysisReport | None
    zoning_report: ZoningReport | None
    litigation_report: LitigationReport | None
    land_records_report: LandRecordsReport | None
    owner_background_report: OwnerBackgroundReport | None
    growth_potential_report: GrowthPotentialReport | None
    zoning_extracts: list[tuple[Path, KudaMasterPlanExtract]] | None
    final_report: RiskReport | None


class PendingUpload(BaseModel):
    """One specialist agent paused, waiting for a human-supplied file.

    Attributes:
        agent: Which node is waiting (e.g. "zoning", "litigation").
        instructions: The adapter's own instructions() text -- exactly
            what to go fetch and where, ready to show a human.
        interrupt_id: LangGraph's id for this specific paused interrupt.
            Pass it back via Orchestrator.provide_upload() to resolve it.
    """

    agent: str
    instructions: str
    interrupt_id: str


class Orchestrator:
    """Runs the six specialist agents (pausing for manual uploads), then risk_report."""

    def __init__(
        self,
        document_agent: DocumentAnalysisAgent,
        provider: LLMProvider | None = None,
        kuda_adapter: KudaMasterPlanAdapter | None = None,
        litigation_adapter: LitigationAdapter | None = None,
        land_records_adapter: LandRecordsAdapter | None = None,
        owner_background_adapter: OwnerBackgroundAdapter | None = None,
        zoning_agent: ZoningAgent | None = None,
        litigation_agent: LitigationAgent | None = None,
        land_records_agent: LandRecordsAgent | None = None,
        owner_background_agent: OwnerBackgroundAgent | None = None,
        growth_potential_agent: GrowthPotentialAgent | None = None,
        risk_report_agent: RiskReportAgent | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        road_network: RoadNetwork | None = None,
    ) -> None:
        """
        Args:
            document_agent: No default -- it needs an LLMProvider (a real
                GeminiProvider, or a FakeProvider in tests), and guessing
                which one silently would hide a real dependency.
            provider: Model backend the default adapters use to read
                uploaded screenshots. Only needed when those defaults are
                used -- passing your own adapters (as the tests do) makes
                it irrelevant.
            kuda_adapter, litigation_adapter, land_records_adapter,
                owner_background_adapter: Default to the real adapters,
                built against `provider`. Tests override them with fake
                adapters that have a scripted parse(), same idea as
                document_agent's FakeProvider.
            road_network: Proposed road network used to measure
                master-plan setbacks. Defaults to whatever
                load_default_network() finds, which is None on a fresh
                clone -- in that case the setback check falls back to
                the screenshot reading, visibly, at lower confidence.
                Tests pass the synthetic fixture explicitly.
            checkpointer: Defaults to InMemorySaver (see
                _default_checkpointer), which is right for tests and
                one-shot runs but dies with the process. Pass
                sqlite_checkpointer(path) for a real check that spans
                days -- that's what the CLI does.
        """
        self.document_agent = document_agent
        self.kuda_adapter = kuda_adapter or KudaMasterPlanAdapter(provider)
        self.litigation_adapter = litigation_adapter or LitigationAdapter(provider)
        self.land_records_adapter = land_records_adapter or LandRecordsAdapter(provider)
        self.owner_background_adapter = owner_background_adapter or OwnerBackgroundAdapter(provider)
        self.zoning_agent = zoning_agent or ZoningAgent()
        self.litigation_agent = litigation_agent or LitigationAgent()
        self.land_records_agent = land_records_agent or LandRecordsAgent()
        self.owner_background_agent = owner_background_agent or OwnerBackgroundAgent()
        self.growth_potential_agent = growth_potential_agent or GrowthPotentialAgent()
        self.risk_report_agent = risk_report_agent or RiskReportAgent()
        # `or` is wrong here: it would re-resolve the default every time a
        # caller deliberately passed a network that happened to be falsy.
        # More importantly, load_default_network() touches the filesystem,
        # so it must not run when the caller already supplied one.
        self.road_network = road_network if road_network is not None else load_default_network()
        self._graph = self._build_graph(checkpointer or _default_checkpointer())

    def _build_graph(self, checkpointer: BaseCheckpointSaver):
        builder = StateGraph(OrchestratorState)
        builder.add_node("document_analysis", self._document_analysis_node)
        builder.add_node("zoning", self._zoning_node)
        builder.add_node("litigation", self._litigation_node)
        builder.add_node("land_records", self._land_records_node)
        builder.add_node("owner_background", self._owner_background_node)
        builder.add_node("growth_potential", self._growth_potential_node)
        builder.add_node("risk_report", self._risk_report_node)

        # Fan out: five nodes start together from START. growth_potential
        # is not here -- it depends on zoning finishing first (reuses its
        # upload), so it's reached via an edge from zoning, not START.
        builder.add_edge(START, "document_analysis")
        builder.add_edge(START, "zoning")
        builder.add_edge(START, "litigation")
        builder.add_edge(START, "land_records")
        builder.add_edge(START, "owner_background")
        builder.add_edge("zoning", "growth_potential")
        # Fan in: risk_report has five inbound edges. zoning is not one
        # of them directly -- a diamond (zoning -> risk_report AND
        # zoning -> growth_potential -> risk_report) let risk_report fire
        # as soon as the direct edge was satisfied, racing ahead of the
        # slower indirect path and reading growth_potential_report as
        # still None (caught by test_final_score_reflects_an_issue_from_one_specialist
        # failing with AttributeError: 'NoneType' object has no attribute
        # 'findings'). zoning's contribution reaches risk_report solely
        # via growth_potential completing -- zoning_report is already in
        # state by then, so nothing is lost, only the race is removed.
        builder.add_edge("document_analysis", "risk_report")
        builder.add_edge("litigation", "risk_report")
        builder.add_edge("land_records", "risk_report")
        builder.add_edge("owner_background", "risk_report")
        builder.add_edge("growth_potential", "risk_report")
        builder.add_edge("risk_report", END)
        return builder.compile(checkpointer=checkpointer)

    def _document_analysis_node(self, state: OrchestratorState) -> dict:
        report = self.document_agent.analyze(state["parcel"], state["pdf_paths"])
        return {"document_report": report}

    def _zoning_node(self, state: OrchestratorState) -> dict:
        instructions = self.kuda_adapter.instructions(state["parcel"])
        upload_path = Path(interrupt({"agent": "zoning", "instructions": instructions}))
        extract = self.kuda_adapter.parse(upload_path)
        extracts = [(upload_path, extract)]
        # Geometry is used only when both halves are present: a network
        # to measure against, and a coordinate to measure from. Missing
        # either is not an error -- the agent falls back to the
        # screenshot-derived answer and says so in the finding.
        location = state.get("location")
        assessment = (
            self.road_network.assess(location)
            if self.road_network is not None and location is not None
            else None
        )
        report = self.zoning_agent.report(
            state["parcel"], extracts, road_assessment=assessment
        )
        return {"zoning_report": report, "zoning_extracts": extracts}

    def _litigation_node(self, state: OrchestratorState) -> dict:
        instructions = self.litigation_adapter.instructions(state["litigation_query"])
        upload_path = Path(interrupt({"agent": "litigation", "instructions": instructions}))
        result = self.litigation_adapter.parse(upload_path)
        report = self.litigation_agent.report(state["parcel"], [(upload_path, result)])
        return {"litigation_report": report}

    def _land_records_node(self, state: OrchestratorState) -> dict:
        instructions = self.land_records_adapter.instructions(state["parcel"])
        upload_path = Path(interrupt({"agent": "land_records", "instructions": instructions}))
        extract = self.land_records_adapter.parse(upload_path)
        report = self.land_records_agent.report(state["parcel"], [(upload_path, extract)])
        return {"land_records_report": report}

    def _owner_background_node(self, state: OrchestratorState) -> dict:
        instructions = self.owner_background_adapter.instructions(state["owner_background_query"])
        upload_path = Path(interrupt({"agent": "owner_background", "instructions": instructions}))
        extract = self.owner_background_adapter.parse(upload_path)
        report = self.owner_background_agent.report(state["parcel"], [(upload_path, extract)])
        return {"owner_background_report": report}

    def _growth_potential_node(self, state: OrchestratorState) -> dict:
        report = self.growth_potential_agent.report(state["parcel"], state["zoning_extracts"])
        return {"growth_potential_report": report}

    def _risk_report_node(self, state: OrchestratorState) -> dict:
        # In a diamond dependency (zoning -> growth_potential -> here, but
        # zoning's OTHER four siblings feed here directly), LangGraph can
        # invoke a join node like this one before every predecessor has
        # actually finished -- confirmed with a minimal reproduction
        # against the installed langgraph version: it runs once early
        # with growth_potential_report still None, then again, correctly,
        # once growth_potential really completes. Only that later
        # invocation's write is what invoke() ultimately returns, so this
        # node must tolerate a still-None report rather than crash on it
        # -- crashing here would abort the whole invoke() before LangGraph
        # ever reaches the correct second call.
        reports = [
            report
            for report in (
                state["document_report"],
                state["zoning_report"],
                state["litigation_report"],
                state["land_records_report"],
                state["owner_background_report"],
                state["growth_potential_report"],
            )
            if report is not None
        ]
        final = self.risk_report_agent.assemble(state["parcel"], reports)
        return {"final_report": final}

    def start(
        self,
        thread_id: str,
        parcel: ParcelIdentifier,
        pdf_paths: list[Path],
        litigation_query: LitigationSearchQuery,
        owner_background_query: OwnerBackgroundQuery,
        location: LatLon | None = None,
    ) -> list[PendingUpload] | RiskReport:
        """Begin a run. Returns pending uploads, or a RiskReport if nothing paused.

        Args:
            thread_id: Caller-chosen id for this run -- pass the same one
                to provide_upload() to resume it. Not generated internally
                so the caller controls how runs map to real-world checks
                (e.g. one thread_id per parcel being checked).
            location: The parcel's coordinate, if known. Without it the
                master-plan setback check falls back to reading a map
                screenshot, so supplying it materially changes how
                trustworthy the headline finding is.
        """
        config = {"configurable": {"thread_id": thread_id}}
        result = self._graph.invoke(
            {
                "parcel": parcel,
                "pdf_paths": pdf_paths,
                "location": location,
                "litigation_query": litigation_query,
                "owner_background_query": owner_background_query,
                "document_report": None,
                "zoning_report": None,
                "litigation_report": None,
                "land_records_report": None,
                "owner_background_report": None,
                "growth_potential_report": None,
                "zoning_extracts": None,
                "final_report": None,
            },
            config=config,
        )
        return self._pending_or_final(result)

    def provide_upload(
        self, thread_id: str, interrupt_id: str, upload_path: Path
    ) -> list[PendingUpload] | RiskReport:
        """Resolve one pending upload and continue. Same return shape as start()."""
        config = {"configurable": {"thread_id": thread_id}}
        result = self._graph.invoke(
            Command(resume={interrupt_id: str(upload_path)}), config=config
        )
        return self._pending_or_final(result)

    def pending_uploads(self, thread_id: str) -> list[PendingUpload]:
        """What this run is still waiting on -- works after a restart too.

        start() and provide_upload() already return this, but a check
        that spans days outlives the process that began it: reopening a
        thread tomorrow needs a way to ask what is still outstanding.

        Filters on task.result being None, which is the part worth
        knowing: get_state() keeps *completed* tasks in its list with
        their interrupts still attached. A run that already resolved
        zoning still shows a zoning interrupt there, so reading
        task.interrupts alone would re-prompt for a file that was
        already supplied. task.result is populated only once the node
        actually finished, so it is the honest signal.
        """
        state = self._graph.get_state({"configurable": {"thread_id": thread_id}})
        return [
            PendingUpload(
                agent=i.value["agent"], instructions=i.value["instructions"], interrupt_id=i.id
            )
            for task in state.tasks
            if task.result is None
            for i in task.interrupts
        ]

    def final_report(self, thread_id: str) -> RiskReport | None:
        """The finished report for a thread, or None if it is still running."""
        state = self._graph.get_state({"configurable": {"thread_id": thread_id}})
        return state.values.get("final_report")

    def _pending_or_final(self, result: dict) -> list[PendingUpload] | RiskReport:
        pending = result.get("__interrupt__")
        if pending:
            return [
                PendingUpload(
                    agent=i.value["agent"], instructions=i.value["instructions"], interrupt_id=i.id
                )
                for i in pending
            ]
        return result["final_report"]
