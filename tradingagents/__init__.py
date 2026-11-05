"""Taiwan TradingAgents: source-grounded Taiwan stock research in Codex."""

__version__ = "0.6.0"

from dotenv import find_dotenv, load_dotenv

# Local data configuration is optional; exported values always take precedence.
# Sponsor credentials normally stay in the trusted GitHub Actions workflow.
load_dotenv(find_dotenv(usecwd=True), override=False)
