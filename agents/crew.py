from __future__ import annotations

from typing import Optional

from crewai import Agent, Crew, LLM, Process, Task

from config import settings
from tools.budget_tools import BudgetQueryTool, BudgetSummaryTool
from tools.congress_tools import CongressmanLookupTool, DistrictToCongressmanTool
from tools.anomaly_tools import AnomalyDetectionTool
from tools.search_tools import WebSearchTool


def _get_llm() -> LLM:
    return LLM(
        model=f"openrouter/{settings.openrouter_model}",
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
    )


def build_crew(region: Optional[str] = None, congressman: Optional[str] = None,
               agency: Optional[str] = None) -> Crew:
    """Build a 5-agent sequential CrewAI crew for spending anomaly analysis."""
    llm = _get_llm()

    context_str = ""
    if region:
        context_str = f"Focus on {region}."
    if congressman:
        context_str += f" Investigate congressman {congressman}."
    if agency:
        context_str += f" Focus on agency: {agency}."

    # Agent 1: Data Ingestion
    ingestion_agent = Agent(
        role="Data Ingestion Specialist",
        goal="Ensure the latest Philippine government budget and congressional data is loaded and accessible.",
        backstory=(
            "You are an expert in Philippine government open data systems. "
            "You know how to access BetterGov.PH, DBM, and Open Congress API data. "
            "Your job is to verify data is fresh and report what data is available."
        ),
        tools=[BudgetQueryTool(), BudgetSummaryTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    ingestion_task = Task(
        description=(
            f"Check that budget and congressional data is loaded in the database. "
            f"{context_str} "
            f"Use the budget_query tool to verify data exists. "
            f"Use budget_summary to get an overview of available data by region and year. "
            f"Report: total records, years covered, regions available."
        ),
        expected_output=(
            "A summary confirming data is available: number of budget records, "
            "years covered, regions present, and any data gaps noted."
        ),
        agent=ingestion_agent,
    )

    # Agent 2: Anomaly Detection
    anomaly_agent = Agent(
        role="Spending Anomaly Analyst",
        goal="Identify statistically significant anomalies in Philippine government budget disbursements.",
        backstory=(
            "You are a forensic budget analyst specializing in Philippine government finances. "
            "You use Isolation Forest and Z-score methods to find unusual spending patterns. "
            "You flag districts with extremely low disbursement rates, sudden budget spikes, "
            "and statistical outliers compared to peer regions."
        ),
        tools=[AnomalyDetectionTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    anomaly_task = Task(
        description=(
            f"Run anomaly detection on Philippine budget data. "
            f"{context_str} "
            f"Use the anomaly_detection tool. Return the top 10 anomalies with their risk scores, "
            f"district codes, agencies, programs, and anomaly reasons. "
            f"Pay special attention to: disbursement rates below 5%, budgets with >200% year-over-year increases, "
            f"and DPWH infrastructure projects in conflict-affected areas."
        ),
        expected_output=(
            "A JSON-formatted list of top 10 anomalies with fields: "
            "district_code, agency, program, risk_score (1-10), "
            "allocation_php, disbursement_rate, anomaly_reason."
        ),
        agent=anomaly_agent,
        context=[ingestion_task],
    )

    # Agent 3: Linkage
    linkage_agent = Agent(
        role="Congressional District Investigator",
        goal="Link budget anomalies to specific congressmen responsible for those districts.",
        backstory=(
            "You are an expert in Philippine congressional districts and representative mappings. "
            "You know that each budget district corresponds to a sitting House representative. "
            "You connect anomalous budget items to the congressman representing that district."
        ),
        tools=[CongressmanLookupTool(), DistrictToCongressmanTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    linkage_task = Task(
        description=(
            "For each anomaly identified by the anomaly analyst, look up the congressman "
            "responsible for that district using the district_to_congressman tool. "
            "For anomalies without a district code, use the congressman_lookup tool to search "
            "by region or province. "
            "Return: congressman name, party, district label, and which anomalies are linked to each rep."
        ),
        expected_output=(
            "A mapping of anomalies to congressmen: for each top anomaly, "
            "the congressman's name, party, district, and risk score."
        ),
        agent=linkage_agent,
        context=[anomaly_task],
    )

    # Agent 4: Research/Validation
    research_agent = Agent(
        role="Government Accountability Researcher",
        goal="Find corroborating public evidence for budget anomalies through news and audit reports.",
        backstory=(
            "You are a transparency researcher who monitors Philippine government accountability. "
            "You search for COA audit findings, news reports, and public records that corroborate "
            "or contextualize budget anomalies. You stick to facts from public sources only."
        ),
        tools=[WebSearchTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    research_task = Task(
        description=(
            "For the top 5 anomalies and their linked congressmen, search for corroborating evidence. "
            "Search for: COA audit findings for the agency/district, news about budget irregularities, "
            "previous fraud cases in the district. "
            "Use specific queries like '[Congressman name] COA audit 2024' or '[Agency] [Region] budget anomaly'. "
            "Note any relevant findings. If no evidence found, state that clearly — do not fabricate."
        ),
        expected_output=(
            "For each of the top 5 flagged items: search findings summary (2-3 sentences), "
            "source URLs if available, or 'No corroborating public evidence found' if search returns nothing relevant."
        ),
        agent=research_agent,
        context=[linkage_task],
    )

    # Agent 5: Report
    report_agent = Agent(
        role="Risk Report Synthesizer",
        goal="Create a clear, factual, Telegram-friendly risk report on Philippine budget anomalies.",
        backstory=(
            "You are a transparency journalist and data analyst who turns complex budget data "
            "into accessible public-interest reports. You present facts clearly, note uncertainty, "
            "and always remind readers these are statistical flags — not proven wrongdoing. "
            "You format reports to fit Telegram message limits."
        ),
        tools=[],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    report_task = Task(
        description=(
            "Synthesize all findings into a clear Telegram-ready report. "
            "Format using HTML-safe text (no <, > characters except for intended HTML tags). "
            "Structure:\n"
            "1. One-line summary of findings\n"
            "2. Top anomalies table: Rank | Congressman | District | Agency | Risk Score | Key Flag\n"
            "3. For each top 3: brief explanation (2 sentences) of why it's flagged\n"
            "4. Disclaimer: 'These are statistical flags based on public budget data. "
            "They indicate patterns that warrant further investigation, not proven wrongdoing.'\n"
            "Keep total output under 3500 characters."
        ),
        expected_output=(
            "A complete Telegram-formatted report with anomaly rankings, congressman links, "
            "risk explanations, and disclaimer. Under 3500 characters total."
        ),
        agent=report_agent,
        context=[research_task],
    )

    crew = Crew(
        agents=[ingestion_agent, anomaly_agent, linkage_agent, research_agent, report_agent],
        tasks=[ingestion_task, anomaly_task, linkage_task, research_task, report_task],
        process=Process.sequential,
        verbose=False,
    )
    return crew
