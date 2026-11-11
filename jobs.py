"""One-shot scheduler for an optional Render Cron Job."""
from app import create_app
from runner import tick

if __name__ == '__main__':
    tick(create_app())
