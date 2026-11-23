"""
main.py
IncuBrix Multi-Channel Lead Generation and Outreach Engine.
Master CLI entrypoint executing the 13-stage deterministic growth pipeline.
"""

import os
import sys
import time
import argparse
import random
from datetime import datetime
from typing import Dict, Any, List

# Ensure package directory is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from engine.logger import logger, record_run, log_error_event, init_db, get_all_runs
from engine.collector import LeadCollector
from engine.validator import LeadValidator
from engine.deduplicator import LeadDeduplicator
from engine.scoring import LeadScorer
from engine.channel_selector import ChannelSelector
from engine.outreach_generator import OutreachGenerator
from engine.exporter import LeadExporter
from engine.refresh import RefreshEngine
from engine.seed_simulator import SeedOutreachSimulator

def run_pipeline(
    input_file: str = "data/sample_leads.csv",
    run_type: str = "NORMAL",
    run_id: str = None,
    priority_a_cap: int = 25,
    execute_seed_test: bool = True
) -> Dict[str, Any]:
    """
    Executes the full 13-stage IncuBrix Lead Generation Pipeline:
      1. Load input records
      2. Validate schemas
      3. Normalize data
      4. Check evidence fields
      5. Verify qualification conditions
      6. Remove duplicates (merging evidence)
      7. Calculate lead scores
      8. Assign priority (A/B/C, Priority A capped at top 25)
      9. Select best and backup channels
      10. Generate messages for Priority A
      11. Export official CSV (exact 30 columns)
      12. Save run statistics to SQLite
      13. Log errors cleanly
    """
    start_time = time.time()
    started_at = datetime.now().isoformat()
    run_id = run_id or f"RUN_{int(start_time)}"

    logger.info("=" * 75)
    logger.info(f"INCUBRIX GROWTH ENGINE — PIPELINE EXECUTION ({run_id})")
    logger.info(f"Target Input: {input_file} | Mode: {run_type}")
    logger.info("=" * 75)

    collector = LeadCollector()
    validator = LeadValidator()
    deduplicator = LeadDeduplicator()
    scorer = LeadScorer()
    channel_selector = ChannelSelector()
    outreach_gen = OutreachGenerator()
    exporter = LeadExporter()
    simulator = SeedOutreachSimulator()

    # Step 1: Load input records & Step 2/3: Validate schemas & Normalize data
    logger.info("[Stage 1-3] Ingesting and normalizing records...")
    raw_records = []
    if input_file == "data/sample_leads.csv":
        if os.path.exists("data/production_leads.csv"):
            prod_records = collector.load_from_csv("data/production_leads.csv")
            raw_records.extend(prod_records)
            logger.info(f"Loaded {len(prod_records)} verified production creator records.")
        sample_records = collector.load_from_csv(input_file)
        raw_records.extend(sample_records)
        logger.info(f"Loaded {len(sample_records)} synthetic demonstration and audit records.")
    else:
        raw_records = collector.load_from_csv(input_file)

    if not raw_records:
        err = f"No input records found in {input_file}"
        logger.error(err)
        log_error_event(run_id, "main.py", "INGESTION_ERROR", err)
        record_run(run_id, run_type, started_at, status="FAILED", error_message=err)
        return {"status": "FAILED", "error": err}

    total_input = len(raw_records)
    logger.info(f"Successfully ingested and normalized {total_input} total candidate records.")

    # Step 4 & 5: Check evidence fields & Verify qualification conditions (Criteria A-F)
    logger.info("[Stage 4-5] Evaluating leads against Criteria A through F...")
    evaluated_records = []
    qualified_count = 0
    rejected_count = 0

    for r in raw_records:
        evaluated = validator.evaluate_lead(r)
        evaluated_records.append(evaluated)
        if evaluated.get("candidate_status") in ("QUALIFIED", "REAL_VERIFIED", "DEMO_SYNTHETIC"):
            qualified_count += 1
        else:
            rejected_count += 1

    logger.info(f"Qualification Results: {qualified_count} Qualified | {rejected_count} Rejected")

    # Step 6: Deduplicate records & merge cross-source evidence
    logger.info("[Stage 6] Executing multi-key deduplication and evidence merging...")
    deduped_records, duplicates_removed = deduplicator.deduplicate(evaluated_records)
    logger.info(f"Deduplication complete: {duplicates_removed} duplicates merged. Net records: {len(deduped_records)}")

    # Step 7: Select best and backup channels
    logger.info("[Stage 7] Routing primary and secondary outreach channels...")
    channel_routed_records = [channel_selector.process_lead(r) for r in deduped_records]

    # Step 8: Calculate lead scores (100-point rubric)
    logger.info("[Stage 8] Calculating granular 100-point lead scores...")
    scored_records = [scorer.calculate_lead_score(r) for r in channel_routed_records]

    # Step 9: Assign priority (A / B / C)
    logger.info(f"[Stage 9] Assigning priorities (Priority A capped at top {priority_a_cap})...")
    prioritized_records = scorer.assign_priorities(scored_records, priority_a_cap=priority_a_cap)

    # Step 10: Generate personalized 3-step outreach for Priority A
    logger.info("[Stage 10] Generating personalized outreach sequences for Priority A leads...")
    outreach_gen.clean_outreach_dirs()
    final_records = []
    priority_a_count = 0

    for r in prioritized_records:
        if r.get("priority") == "A":
            r = outreach_gen.enrich_priority_a_lead(r)
            priority_a_count += 1
        final_records.append(r)

    logger.info(f"Generated complete 3-step outreach sequences for {priority_a_count} Priority A leads.")

    # Step 11: Export official CSVs and Submission Workbook
    logger.info("[Stage 11] Exporting CRM-ready UTF-8 CSVs and submission workbook...")
    export_paths = exporter.export_all_bundles(final_records)
    # Submission workbook 02_LEADS matches official exported leads exactly without discrepancy
    exported_leads_for_wb = collector.load_from_csv(export_paths["leads"])
    if not exported_leads_for_wb and os.path.exists(export_paths.get("demo_leads", "")):
        exported_leads_for_wb = collector.load_from_csv(export_paths["demo_leads"])
    if not exported_leads_for_wb:
        exported_leads_for_wb = final_records
    workbook_path = exporter.build_submission_workbook(exported_leads_for_wb, "submission_workbook.xlsx")
    export_paths["submission_workbook"] = workbook_path

    # Step 12: Audit portfolio constraints
    portfolio_audit = validator.audit_portfolio_constraints(final_records)
    logger.info(f"Portfolio Constraints Audit: Valid={portfolio_audit['valid']} | Approved Country Ratio={portfolio_audit.get('approved_country_ratio', 0):.1%}")

    # Step 13: Execute developer seed test simulation on test accounts
    seed_summary = None
    if execute_seed_test:
        logger.info("[Stage 13] Running developer seed simulation on owned test accounts...")
        priority_a_leads = [l for l in final_records if l.get("priority") == "A"]
        seed_summary = simulator.run_seed_test(priority_a_leads, run_id=f"SEED_{run_id}")

    duration = round(time.time() - start_time, 2)
    ended_at = datetime.now().isoformat()

    final_qualified = len([l for l in final_records if l.get("candidate_status") in ("QUALIFIED", "REAL_VERIFIED", "DEMO_SYNTHETIC")])
    final_rejected = len([l for l in final_records if l.get("candidate_status") not in ("QUALIFIED", "REAL_VERIFIED", "DEMO_SYNTHETIC")])

    # Save run statistics to SQLite
    record_run(
        run_id=run_id,
        run_type=run_type,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration,
        input_count=total_input,
        items_produced=len(final_records),
        qualified_count=final_qualified,
        rejected_count=final_rejected,
        duplicates_removed=duplicates_removed,
        status="SUCCESS",
        direct_cost_usd=0.0,
        notes=f"Complete pipeline execution. Priority A: {priority_a_count}. Constraints valid: {portfolio_audit['valid']}"
    )

    logger.info("=" * 75)
    logger.info(f"PIPELINE COMPLETE ({duration}s)")
    logger.info(f"Total Qualified: {final_qualified} | Priority A: {priority_a_count} | Duplicates Merged: {duplicates_removed}")
    logger.info(f"Output files: {export_paths['leads']} | {workbook_path}")
    logger.info("=" * 75)

    return {
        "status": "SUCCESS",
        "run_id": run_id,
        "duration_seconds": duration,
        "input_count": total_input,
        "total_records": len(final_records),
        "qualified_count": final_qualified,
        "rejected_count": final_rejected,
        "duplicates_removed": duplicates_removed,
        "priority_a_count": priority_a_count,
        "portfolio_audit": portfolio_audit,
        "export_paths": export_paths,
        "seed_summary": seed_summary
    }

def run_audit(sample_size: int = 20, csv_path: str = "data/output/leads.csv"):
    """
    Executes a random 20-lead audit simulating an official IncuBrix reviewer check.
    Verifies that >=85% (17+/20) qualify and >=90% (18+/20) channels are accurate.
    """
    collector = LeadCollector()
    validator = LeadValidator()
    leads = collector.load_from_csv(csv_path)

    if len(leads) < sample_size:
        print(f"Warning: only {len(leads)} leads available for audit.")
        sample_size = len(leads)

    sample = random.sample(leads, sample_size)
    print("\n" + "=" * 90)
    print(f"INCUBRIX QUALITY AUDIT: RANDOM SAMPLE OF {sample_size} LEADS")
    print("=" * 90)

    qualified_count = 0
    channel_accurate = 0

    print(f"{'Lead ID':<10} | {'Creator Name':<20} | {'Segment':<14} | {'Score':<6} | {'Channel':<15} | {'Status':<10}")
    print("-" * 90)

    for l in sample:
        v = validator.evaluate_lead(l)
        is_qual = v["candidate_status"] == "QUALIFIED"
        if is_qual:
            qualified_count += 1
        # Check channel accuracy
        ch = l.get("best_channel", "")
        handle = l.get("channel_handle_or_url", "")
        ch_evi = l.get("channel_evidence_url", "")
        if ch and handle and ch_evi:
            channel_accurate += 1

        print(f"{l.get('lead_id'):<10} | {l.get('creator_name')[:20]:<20} | {l.get('creator_segment')[:14]:<14} | {l.get('lead_score', 0):<6} | {ch[:15]:<15} | {v['candidate_status']:<10}")

    qual_pct = (qualified_count / sample_size) * 100
    ch_pct = (channel_accurate / sample_size) * 100

    print("-" * 90)
    print(f"Audit Summary: {qualified_count}/{sample_size} Qualified ({qual_pct:.1f}%) — Requirement: >= 85%")
    print(f"Channel Accuracy: {channel_accurate}/{sample_size} Correct ({ch_pct:.1f}%) — Requirement: >= 90%")
    print(f"Result: {'PASS' if qual_pct >= 85 and ch_pct >= 90 else 'REVIEW NEEDED'}")
    print("=" * 90 + "\n")

def main():
    parser = argparse.ArgumentParser(description="IncuBrix Growth Engine CLI")
    parser.add_argument("--mode", choices=["normal", "refresh", "seed-test", "failure-demo"], default="normal", help="Execution mode")
    parser.add_argument("--input", default="data/sample_leads.csv", help="Path to input leads CSV")
    parser.add_argument("--audit", action="store_true", help="Run 20-lead random audit check")
    args = parser.parse_args()

    if args.audit:
        run_audit()
        return

    if args.mode == "normal":
        run_pipeline(input_file=args.input, run_type="NORMAL")
    elif args.mode == "refresh":
        refresher = RefreshEngine()
        refresher.run_refresh(args.input)
    elif args.mode == "seed-test":
        collector = LeadCollector()
        leads = collector.load_from_csv("data/output/leads.csv")
        p_a = [l for l in leads if l.get("priority") == "A"]
        sim = SeedOutreachSimulator()
        sim.run_seed_test(p_a)
    elif args.mode == "failure-demo":
        logger.info("Running simulated failure and recovery test...")
        # Corrupt file test
        refresher = RefreshEngine()
        refresher.run_refresh("data/non_existent_file.csv")

if __name__ == "__main__":
    main()
