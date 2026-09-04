import os
import shutil
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

console = Console()

class EnvSwitcher:
    """
    Safely creates .env.production and switches local .env to point to the new database
    with automatic timestamped backups of the original .env.
    """

    def __init__(self, env_file_path: str = ".env"):
        self.env_path = Path(env_file_path)

    def generate_production_env(self, new_db_url: str) -> str:
        """Creates .env.production with the new database URL."""
        prod_env_path = self.env_path.parent / ".env.production"
        
        content = f"""# ==============================================================================
# PRODUCTION ENVIRONMENT CONFIGURATION (POST-MIGRATION)
# Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# ==============================================================================

DATABASE_URL={new_db_url}
"""
        with open(prod_env_path, "w") as f:
            f.write(content)

        console.print(f"[bold green]✔ Generated new production config:[/bold green] [cyan]{prod_env_path}[/cyan]")
        return str(prod_env_path)

    def switch_env(self, new_db_url: str) -> bool:
        """Backs up existing .env and switches DATABASE_URL in .env to the new provider."""
        if not self.env_path.exists():
            console.print("[yellow]⚠️ .env file not found, creating new .env...[/yellow]")
            with open(self.env_path, "w") as f:
                f.write(f"DATABASE_URL={new_db_url}\n")
            return True

        # 1. Create timestamped backup of current .env
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_env = self.env_path.parent / f".env.backup_{timestamp}"
        shutil.copyfile(self.env_path, backup_env)
        console.print(f"  Saved backup of current .env to: [yellow]{backup_env}[/yellow]")

        # 2. Read lines and update
        with open(self.env_path, "r") as f:
            lines = f.readlines()

        updated_lines = []
        db_updated = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("DATABASE_URL=") or stripped.startswith("DB="):
                updated_lines.append(f"DATABASE_URL={new_db_url}\n")
                db_updated = True
            else:
                updated_lines.append(line)

        if not db_updated:
            updated_lines.append(f"\nDATABASE_URL={new_db_url}\n")

        with open(self.env_path, "w") as f:
            f.writelines(updated_lines)

        console.print(Panel(
            f"[bold green]✔ .env has been successfully updated![/bold green]\n\n"
            f"• Original backup: [yellow]{backup_env.name}[/yellow]\n"
            f"• Active DATABASE_URL now points to the new provider.",
            title="⚙️ Config Switched",
            border_style="green"
        ))
        return True
