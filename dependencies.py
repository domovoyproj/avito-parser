"""FastAPI dependencies with explicit override points for isolated tests."""

from database import Database, db
from monitoring import monitor_service


def get_database() -> Database:
    return db


def get_monitoring_service():
    return monitor_service


def get_parser_jobs():
    from parser_jobs import job_manager
    return job_manager


def get_http_engine():
    from http_engine import http_engine
    return http_engine


def get_browser_engine():
    from browser_engine import browser_engine
    return browser_engine


def get_export_jobs():
    from export_jobs import export_jobs
    return export_jobs
