"""Paperclip integration with Dispatch adapter for command execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from app.dispatch_adapter import CommandResult, FallbackExecutionAdapter

logger = logging.getLogger(__name__)


class PaperclipDispatchIntegration:
    """Integration layer between Paperclip and Dispatch execution channels."""

    def __init__(self, config_path: str | None = None):
        """Initialize Paperclip Dispatch integration.

        Args:
            config_path: Path to JSON configuration file. If not provided,
                        configuration is loaded from environment variables.
        """
        self.config = self._load_config(config_path)
        self.adapter: FallbackExecutionAdapter | None = None
        self._initialized = False

    def _load_config(self, config_path: str | None) -> dict[str, Any]:
        """Load configuration from file or environment."""
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)

        # Load from environment variables
        return {
            "ssh": {
                "host": os.getenv("DISPATCH_SSH_HOST", "localhost"),
                "port": os.getenv("DISPATCH_SSH_PORT", "22"),
                "user": os.getenv("DISPATCH_SSH_USER", "root"),
                "key_path": os.getenv("DISPATCH_SSH_KEY_PATH"),
            },
            "dispatch": {
                "url": os.getenv(
                    "DISPATCH_URL", "http://localhost:7654"
                ),
                "api_key": os.getenv("DISPATCH_API_KEY"),
                "timeout": os.getenv("DISPATCH_TIMEOUT", "30"),
            },
        }

    async def initialize(self) -> bool:
        """Initialize the Dispatch adapter.

        Returns:
            True if initialization successful, False otherwise.
        """
        try:
            self.adapter = FallbackExecutionAdapter(
                ssh_config=self.config.get("ssh"),
                dispatch_config=self.config.get("dispatch"),
            )
            self._initialized = True
            logger.info("Paperclip Dispatch integration initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Dispatch integration: {e}")
            return False

    async def execute_command(
        self, command: str, timeout_sec: int = 30
    ) -> dict[str, Any]:
        """Execute a command through the Dispatch adapter.

        Args:
            command: Command to execute
            timeout_sec: Timeout in seconds

        Returns:
            Dictionary containing execution result.
        """
        if not self._initialized or not self.adapter:
            return {
                "success": False,
                "error": "Integration not initialized",
                "channel": "none",
            }

        try:
            result = await self.adapter.execute(command, timeout_sec)
            return result.to_dict()
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "channel": "none",
            }

    async def get_execution_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent execution logs.

        Args:
            limit: Maximum number of logs to return

        Returns:
            List of execution log dictionaries.
        """
        if not self._initialized or not self.adapter:
            return []

        return self.adapter.get_execution_log(limit)

    async def get_statistics(self) -> dict[str, Any]:
        """Get execution statistics.

        Returns:
            Dictionary containing execution statistics.
        """
        if not self._initialized or not self.adapter:
            return {
                "total_executions": 0,
                "successful": 0,
                "failed": 0,
                "success_rate": 0,
            }

        return self.adapter.get_statistics()

    async def health_check(self) -> dict[str, Any]:
        """Check health of both execution channels.

        Returns:
            Dictionary with channel availability status.
        """
        if not self._initialized or not self.adapter:
            return {"initialized": False}

        ssh_available = False
        dispatch_available = False

        try:
            if self.adapter.ssh_channel:
                ssh_available = await self.adapter.ssh_channel.is_available()
        except Exception as e:
            logger.warning(f"SSH health check failed: {e}")

        try:
            if self.adapter.dispatch_channel:
                dispatch_available = (
                    await self.adapter.dispatch_channel.is_available()
                )
        except Exception as e:
            logger.warning(f"Dispatch health check failed: {e}")

        return {
            "initialized": True,
            "ssh_available": ssh_available,
            "dispatch_available": dispatch_available,
            "preferred_channel": (
                "ssh" if ssh_available else "dispatch" if dispatch_available else "none"
            ),
        }

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self.adapter:
            await self.adapter.close()
            self._initialized = False
            logger.info("Paperclip Dispatch integration cleaned up")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()


async def execute_with_fallback(
    command: str,
    ssh_config: dict[str, str] | None = None,
    dispatch_config: dict[str, str] | None = None,
    timeout_sec: int = 30,
) -> CommandResult:
    """Convenience function to execute a command with fallback.

    Args:
        command: Command to execute
        ssh_config: SSH configuration dictionary
        dispatch_config: Dispatch configuration dictionary
        timeout_sec: Timeout in seconds

    Returns:
        CommandResult object.
    """
    adapter = FallbackExecutionAdapter(
        ssh_config=ssh_config,
        dispatch_config=dispatch_config,
    )
    try:
        return await adapter.execute(command, timeout_sec)
    finally:
        await adapter.close()


async def main():
    """Example usage of Paperclip Dispatch integration."""
    # Example 1: Using context manager
    async with PaperclipDispatchIntegration() as integration:
        health = await integration.health_check()
        print(f"Health: {json.dumps(health, indent=2)}")

        result = await integration.execute_command("echo 'Hello Dispatch'")
        print(f"Result: {json.dumps(result, indent=2)}")

        stats = await integration.get_statistics()
        print(f"Stats: {json.dumps(stats, indent=2)}")

    # Example 2: Using convenience function
    result = await execute_with_fallback(
        "ls -la /tmp",
        ssh_config={
            "host": "localhost",
            "user": "user",
        },
        dispatch_config={
            "url": "http://localhost:7654",
        },
    )
    print(f"Command result: {result.to_dict()}")


if __name__ == "__main__":
    asyncio.run(main())
