"""QRESPONDER local web review UI (Phase 4).

A thin layer over the engine: it calls run_pipeline / approve_one / writer /
writeback and reimplements NO grounding, answering, or guardrail logic. FastAPI
and uvicorn are imported lazily by the `serve` CLI command so the slim image and
the import guard stay intact.
"""
