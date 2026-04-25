"""Dispatch channel adapter with SSH fallback for Paperclip agent integration."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of command execution."""

    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    channel: str  # 'ssh' or 'dispatch'
    execution_time_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "channel": self.channel,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp.isoformat(),
        }


class ExecutionChannel(ABC):
    """Abstract base for execution channels (SSH, Dispatch, etc)."""

    @abstractmethod
    async def execute(self, command: str, timeout_sec: int = 30) -> CommandResult:
        """Execute command and return result."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if channel is available."""
        pass


class SSHChannel(ExecutionChannel):
    """SSH-based command execution."""

    def __init__(self, host: str, port: int = 22, user: str = "root", key_path: str | None = None):
        self.host = host
        self.port = port
        self.user = user
        self.key_path = key_path
        self.connection_timeout = 5

    async def is_available(self) -> bool:
        """Check SSH connectivity."""
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, self._check_ssh_connection),
                timeout=self.connection_timeout,
            )
            return True
        except (asyncio.TimeoutError, OSError, subprocess.CalledProcessError) as e:
            logger.warning(f"SSH connection check failed for {self.host}: {e}")
            return False

    def _check_ssh_connection(self) -> bool:
        """Synchronous SSH connection check using ssh -O check."""
        try:
            cmd = ["ssh", "-O", "check", f"{self.user}@{self.host}"]
            subprocess.run(cmd, timeout=self.connection_timeout, capture_output=True, check=False)
            # ssh -O check returns 0 if connection exists, non-zero otherwise
            return True
        except subprocess.TimeoutExpired:
            return False

    async def execute(self, command: str, timeout_sec: int = 30) -> CommandResult:
        """Execute command via SSH."""
        start_time = datetime.now(timezone.utc)
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_ssh_command, command, timeout_sec),
                timeout=timeout_sec + 2,
            )
            result.execution_time_ms = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"SSH command timed out after {timeout_sec}s")
            execution_time = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"Command timed out after {timeout_sec} seconds",
                channel="ssh",
                execution_time_ms=execution_time,
            )
        except Exception as e:
            logger.error(f"SSH execution failed: {e}")
            execution_time = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=str(e),
                channel="ssh",
                execution_time_ms=execution_time,
            )

    def _run_ssh_command(self, command: str, timeout_sec: int) -> CommandResult:
        """Synchronous SSH command execution."""
        try:
            key_args = []
            if self.key_path:
                key_args = ["-i", self.key_path]

            ssh_cmd = [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                *key_args,
                f"{self.user}@{self.host}",
                command,
            ]

            result = subprocess.run(
                ssh_cmd,
                timeout=timeout_sec,
                capture_output=True,
                text=True,
            )

            return CommandResult(
                success=result.returncode == 0,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                channel="ssh",
                execution_time_ms=0,
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"SSH command timed out: {e}",
                channel="ssh",
                execution_time_ms=0,
            )
        except Exception as e:
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"SSH execution error: {e}",
                channel="ssh",
                execution_time_ms=0,
            )


class DispatchChannel(ExecutionChannel):
    """Dispatch-based command execution via Claude Desktop Dispatch."""

    def __init__(
        self,
        dispatch_url: str,
        api_key: str | None = None,
        timeout: int = 30,
    ):
        self.dispatch_url = dispatch_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def is_available(self) -> bool:
        """Check Dispatch availability."""
        try:
            response = await self.client.get(f"{self.dispatch_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Dispatch health check failed: {e}")
            return False

    async def execute(self, command: str, timeout_sec: int = 30) -> CommandResult:
        """Execute command via Dispatch."""
        start_time = datetime.now(timezone.utc)
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload = {"command": command, "timeout": timeout_sec}

            response = await self.client.post(
                f"{self.dispatch_url}/api/execute",
                json=payload,
                headers=headers,
                timeout=timeout_sec + 5,
            )

            execution_time = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )

            if response.status_code == 200:
                result_data = response.json()
                return CommandResult(
                    success=result_data.get("exit_code") == 0,
                    exit_code=result_data.get("exit_code"),
                    stdout=result_data.get("stdout", ""),
                    stderr=result_data.get("stderr", ""),
                    channel="dispatch",
                    execution_time_ms=execution_time,
                )
            else:
                logger.error(f"Dispatch API error: {response.status_code}")
                return CommandResult(
                    success=False,
                    exit_code=response.status_code,
                    stdout="",
                    stderr=f"Dispatch API returned {response.status_code}",
                    channel="dispatch",
                    execution_time_ms=execution_time,
                )

        except asyncio.TimeoutError:
            logger.error(f"Dispatch command timed out after {timeout_sec}s")
            execution_time = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"Dispatch request timed out after {timeout_sec} seconds",
                channel="dispatch",
                execution_time_ms=execution_time,
            )
        except Exception as e:
            logger.error(f"Dispatch execution failed: {e}")
            execution_time = (
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr=str(e),
                channel="dispatch",
                execution_time_ms=execution_time,
            )

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


class FallbackExecutionAdapter:
    """Adapter that tries SSH first, then falls back to Dispatch on failure."""

    def __init__(
        self,
        ssh_config: dict[str, str] | None = None,
        dispatch_config: dict[str, str] | None = None,
    ):
        self.ssh_channel: SSHChannel | None = None
        self.dispatch_channel: DispatchChannel | None = None
        self.fallback_enabled = True
        self.execution_log: list[CommandResult] = []

        if ssh_config:
            self.ssh_channel = SSHChannel(
                host=ssh_config.get("host", "localhost"),
                port=int(ssh_config.get("port", 22)),
                user=ssh_config.get("user", "root"),
                key_path=ssh_config.get("key_path"),
            )

        if dispatch_config:
            self.dispatch_channel = DispatchChannel(
                dispatch_url=dispatch_config.get("url", "http://localhost:7654"),
                api_key=dispatch_config.get("api_key"),
                timeout=int(dispatch_config.get("timeout", 30)),
            )

    async def execute(self, command: str, timeout_sec: int = 30) -> CommandResult:
        """Execute command with fallback strategy."""
        result: CommandResult | None = None

        # Try SSH first if available
        if self.ssh_channel:
            ssh_available = await self.ssh_channel.is_available()
            if ssh_available:
                logger.info(f"Executing via SSH: {command[:100]}")
                result = await self.ssh_channel.execute(command, timeout_sec)
                self.execution_log.append(result)

                if result.success:
                    logger.info(f"SSH execution succeeded (channel={result.channel})")
                    return result
                else:
                    logger.warning(
                        f"SSH execution failed: {result.stderr[:100]}"
                    )

        # Fall back to Dispatch if enabled and SSH failed
        if self.fallback_enabled and self.dispatch_channel:
            logger.info(f"Falling back to Dispatch: {command[:100]}")
            dispatch_available = await self.dispatch_channel.is_available()
            if dispatch_available:
                result = await self.dispatch_channel.execute(command, timeout_sec)
                self.execution_log.append(result)

                if result.success:
                    logger.info("Dispatch execution succeeded")
                    return result
                else:
                    logger.error(
                        f"Dispatch execution failed: {result.stderr[:100]}"
                    )

        # If nothing worked, return last error or generic failure
        if result:
            return result
        else:
            return CommandResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr="No execution channel available (SSH unavailable, Dispatch disabled or unavailable)",
                channel="none",
                execution_time_ms=0,
            )

    async def close(self) -> None:
        """Clean up resources."""
        if self.dispatch_channel:
            await self.dispatch_channel.close()

    def get_execution_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent execution logs."""
        return [result.to_dict() for result in self.execution_log[-limit:]]

    def get_statistics(self) -> dict[str, Any]:
        """Get execution statistics."""
        total = len(self.execution_log)
        successful = sum(1 for r in self.execution_log if r.success)
        ssh_count = sum(1 for r in self.execution_log if r.channel == "ssh")
        dispatch_count = sum(1 for r in self.execution_log if r.channel == "dispatch")

        avg_time = (
            sum(r.execution_time_ms for r in self.execution_log) / total
            if total > 0
            else 0
        )

        return {
            "total_executions": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "ssh_executions": ssh_count,
            "dispatch_executions": dispatch_count,
            "avg_execution_time_ms": avg_time,
        }
