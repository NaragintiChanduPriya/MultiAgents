"""Convenience launcher for Google ADK Dev UI with all hospital agents."""

import os
import sys
from pathlib import Path

# Ensure local model cost map fallback to prevent network timeouts
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if __name__ == "__main__":
    from google.adk.cli.cli_tools_click import cli_web

    agents_dir = str(PROJECT_ROOT / "agents")
    print(f"Starting Google ADK Web UI pointing to agents directory: {agents_dir}")
    print("Available agents:")
    print("  - appointment_agent")
    print("  - discharge_summary_report_agent")
    print("  - general_hospital_agent")
    print("  - medication_agent")
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    print(f"\nAccess Dev UI at: http://127.0.0.1:{port}/dev-ui/")
    
    try:
        cli_web.main(args=[agents_dir, "--port", str(port)], standalone_mode=False)
    except KeyboardInterrupt:
        print("\nADK Web UI stopped.")
        sys.exit(0)

