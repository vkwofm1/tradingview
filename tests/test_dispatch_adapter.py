"""Tests for Dispatch adapter with SSH fallback."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dispatch_adapter import (
    CommandResult,
    DispatchChannel,
    FallbackExecutionAdapter,
    SSHChannel,
)


class TestCommandResult:
    """Test CommandResult dataclass."""

    def test_command_result_to_dict(self):
        """Test CommandResult serialization to dict."""
        result = CommandResult(
            success=True,
            exit_code=0,
            stdout="output",
            stderr="",
            channel="ssh",
            execution_time_ms=100.5,
        )

        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["exit_code"] == 0
        assert result_dict["stdout"] == "output"
        assert result_dict["channel"] == "ssh"
        assert "timestamp" in result_dict


class TestSSHChannel:
    """Test SSH channel implementation."""

    @pytest.fixture
    def ssh_channel(self):
        return SSHChannel(
            host="localhost",
            port=22,
            user="root",
            key_path=None,
        )

    @pytest.mark.asyncio
    async def test_ssh_is_available_success(self, ssh_channel):
        """Test successful SSH availability check."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = await ssh_channel.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_ssh_is_available_failure(self, ssh_channel):
        """Test failed SSH availability check."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Connection refused")
            result = await ssh_channel.is_available()
            assert result is False

    @pytest.mark.asyncio
    async def test_ssh_execute_success(self, ssh_channel):
        """Test successful SSH command execution."""
        with patch.object(ssh_channel, "_run_ssh_command") as mock_exec:
            mock_exec.return_value = CommandResult(
                success=True,
                exit_code=0,
                stdout="test output",
                stderr="",
                channel="ssh",
                execution_time_ms=50,
            )

            result = await ssh_channel.execute("echo test")

            assert result.success is True
            assert result.exit_code == 0
            assert result.stdout == "test output"
            assert result.channel == "ssh"

    @pytest.mark.asyncio
    async def test_ssh_execute_timeout(self, ssh_channel):
        """Test SSH command timeout."""
        with patch.object(ssh_channel, "_run_ssh_command") as mock_exec:
            mock_exec.side_effect = asyncio.TimeoutError()
            # Simulate timeout in execute method
            with patch("asyncio.wait_for") as mock_wait:
                mock_wait.side_effect = asyncio.TimeoutError()
                result = await ssh_channel.execute("sleep 100", timeout_sec=5)

                assert result.success is False
                assert result.exit_code is None
                assert "timed out" in result.stderr.lower()
                assert result.channel == "ssh"


class TestDispatchChannel:
    """Test Dispatch channel implementation."""

    @pytest.fixture
    def dispatch_channel(self):
        return DispatchChannel(
            dispatch_url="http://localhost:7654",
            api_key="test-key",
            timeout=30,
        )

    @pytest.mark.asyncio
    async def test_dispatch_is_available_success(self, dispatch_channel):
        """Test successful Dispatch availability check."""
        with patch.object(dispatch_channel.client, "get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = await dispatch_channel.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_dispatch_is_available_failure(self, dispatch_channel):
        """Test failed Dispatch availability check."""
        with patch.object(dispatch_channel.client, "get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")

            result = await dispatch_channel.is_available()
            assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_execute_success(self, dispatch_channel):
        """Test successful Dispatch command execution."""
        with patch.object(dispatch_channel.client, "post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "exit_code": 0,
                "stdout": "dispatch output",
                "stderr": "",
            }

            async def async_post(*args, **kwargs):
                return mock_response

            mock_post.side_effect = async_post
            result = await dispatch_channel.execute("echo test")

            assert result.success is True
            assert result.exit_code == 0
            assert result.stdout == "dispatch output"
            assert result.channel == "dispatch"

    @pytest.mark.asyncio
    async def test_dispatch_execute_failure(self, dispatch_channel):
        """Test failed Dispatch command execution."""
        with patch.object(dispatch_channel.client, "post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            result = await dispatch_channel.execute("bad command")

            assert result.success is False
            assert result.exit_code == 500
            assert "500" in result.stderr

    @pytest.mark.asyncio
    async def test_dispatch_execute_timeout(self, dispatch_channel):
        """Test Dispatch command timeout."""
        async def async_post_timeout(*args, **kwargs):
            raise asyncio.TimeoutError("Request timeout")

        with patch.object(dispatch_channel.client, "post", new=async_post_timeout):
            result = await dispatch_channel.execute("long command", timeout_sec=5)

            assert result.success is False
            assert ("timed out" in result.stderr.lower() or
                    "timeout" in result.stderr.lower() or
                    "request timeout" in result.stderr.lower())


class TestFallbackExecutionAdapter:
    """Test FallbackExecutionAdapter with SSH and Dispatch fallback."""

    @pytest.fixture
    def adapter(self):
        return FallbackExecutionAdapter(
            ssh_config={
                "host": "localhost",
                "port": "22",
                "user": "root",
            },
            dispatch_config={
                "url": "http://localhost:7654",
                "api_key": "test-key",
            },
        )

    @pytest.mark.asyncio
    async def test_execute_ssh_success(self, adapter):
        """Test execution succeeds on SSH first try."""
        with patch.object(adapter.ssh_channel, "is_available") as mock_available:
            mock_available.return_value = True
            with patch.object(adapter.ssh_channel, "execute") as mock_execute:
                expected_result = CommandResult(
                    success=True,
                    exit_code=0,
                    stdout="ssh output",
                    stderr="",
                    channel="ssh",
                    execution_time_ms=50,
                )
                mock_execute.return_value = expected_result

                result = await adapter.execute("echo test")

                assert result.success is True
                assert result.channel == "ssh"
                assert len(adapter.execution_log) == 1

    @pytest.mark.asyncio
    async def test_execute_ssh_fails_fallback_dispatch_success(self, adapter):
        """Test fallback to Dispatch when SSH fails."""
        with patch.object(adapter.ssh_channel, "is_available") as mock_ssh_avail:
            mock_ssh_avail.return_value = True
            with patch.object(adapter.ssh_channel, "execute") as mock_ssh_exec:
                # SSH fails
                ssh_failure = CommandResult(
                    success=False,
                    exit_code=1,
                    stdout="",
                    stderr="ssh error",
                    channel="ssh",
                    execution_time_ms=100,
                )
                mock_ssh_exec.return_value = ssh_failure

                with patch.object(
                    adapter.dispatch_channel, "is_available"
                ) as mock_dispatch_avail:
                    mock_dispatch_avail.return_value = True
                    with patch.object(
                        adapter.dispatch_channel, "execute"
                    ) as mock_dispatch_exec:
                        # Dispatch succeeds
                        dispatch_success = CommandResult(
                            success=True,
                            exit_code=0,
                            stdout="dispatch output",
                            stderr="",
                            channel="dispatch",
                            execution_time_ms=150,
                        )
                        mock_dispatch_exec.return_value = dispatch_success

                        result = await adapter.execute("echo test")

                        assert result.success is True
                        assert result.channel == "dispatch"
                        assert len(adapter.execution_log) == 2
                        assert adapter.execution_log[0].channel == "ssh"
                        assert adapter.execution_log[1].channel == "dispatch"

    @pytest.mark.asyncio
    async def test_execute_ssh_unavailable_dispatch_success(self, adapter):
        """Test execution skips SSH if unavailable and uses Dispatch."""
        with patch.object(adapter.ssh_channel, "is_available") as mock_ssh_avail:
            mock_ssh_avail.return_value = False

            with patch.object(
                adapter.dispatch_channel, "is_available"
            ) as mock_dispatch_avail:
                mock_dispatch_avail.return_value = True
                with patch.object(
                    adapter.dispatch_channel, "execute"
                ) as mock_dispatch_exec:
                    dispatch_success = CommandResult(
                        success=True,
                        exit_code=0,
                        stdout="dispatch output",
                        stderr="",
                        channel="dispatch",
                        execution_time_ms=150,
                    )
                    mock_dispatch_exec.return_value = dispatch_success

                    result = await adapter.execute("echo test")

                    assert result.success is True
                    assert result.channel == "dispatch"
                    assert len(adapter.execution_log) == 1
                    assert adapter.execution_log[0].channel == "dispatch"

    @pytest.mark.asyncio
    async def test_execute_both_fail(self, adapter):
        """Test execution fails when both SSH and Dispatch fail."""
        with patch.object(adapter.ssh_channel, "is_available") as mock_ssh_avail:
            mock_ssh_avail.return_value = True
            with patch.object(adapter.ssh_channel, "execute") as mock_ssh_exec:
                ssh_failure = CommandResult(
                    success=False,
                    exit_code=1,
                    stdout="",
                    stderr="ssh error",
                    channel="ssh",
                    execution_time_ms=100,
                )
                mock_ssh_exec.return_value = ssh_failure

                with patch.object(
                    adapter.dispatch_channel, "is_available"
                ) as mock_dispatch_avail:
                    mock_dispatch_avail.return_value = True
                    with patch.object(
                        adapter.dispatch_channel, "execute"
                    ) as mock_dispatch_exec:
                        dispatch_failure = CommandResult(
                            success=False,
                            exit_code=500,
                            stdout="",
                            stderr="dispatch error",
                            channel="dispatch",
                            execution_time_ms=150,
                        )
                        mock_dispatch_exec.return_value = dispatch_failure

                        result = await adapter.execute("echo test")

                        assert result.success is False
                        assert result.channel == "dispatch"
                        assert "dispatch error" in result.stderr

    def test_get_execution_log(self, adapter):
        """Test execution log retrieval."""
        result1 = CommandResult(
            success=True,
            exit_code=0,
            stdout="out1",
            stderr="",
            channel="ssh",
            execution_time_ms=50,
        )
        result2 = CommandResult(
            success=False,
            exit_code=1,
            stdout="",
            stderr="err2",
            channel="dispatch",
            execution_time_ms=100,
        )

        adapter.execution_log = [result1, result2]

        log = adapter.get_execution_log()
        assert len(log) == 2
        assert log[0]["success"] is True
        assert log[1]["success"] is False

    def test_get_statistics(self, adapter):
        """Test statistics calculation."""
        results = [
            CommandResult(
                success=True,
                exit_code=0,
                stdout="out",
                stderr="",
                channel="ssh",
                execution_time_ms=100,
            ),
            CommandResult(
                success=True,
                exit_code=0,
                stdout="out",
                stderr="",
                channel="dispatch",
                execution_time_ms=200,
            ),
            CommandResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr="err",
                channel="ssh",
                execution_time_ms=50,
            ),
        ]

        adapter.execution_log = results

        stats = adapter.get_statistics()

        assert stats["total_executions"] == 3
        assert stats["successful"] == 2
        assert stats["failed"] == 1
        assert stats["success_rate"] == pytest.approx(66.67, abs=1)
        assert stats["ssh_executions"] == 2
        assert stats["dispatch_executions"] == 1
        assert stats["avg_execution_time_ms"] == pytest.approx(116.67, abs=0.1)


@pytest.mark.asyncio
async def test_integration_ssh_fallback_workflow():
    """Integration test for complete SSH-to-Dispatch fallback workflow."""
    adapter = FallbackExecutionAdapter(
        ssh_config={"host": "remote.example.com", "port": "22", "user": "admin"},
        dispatch_config={"url": "http://dispatch.local:7654", "api_key": "key123"},
    )

    # Simulate SSH unavailable
    with patch.object(adapter.ssh_channel, "is_available") as mock_ssh_avail:
        mock_ssh_avail.return_value = False

        # Simulate Dispatch available and successful
        with patch.object(
            adapter.dispatch_channel, "is_available"
        ) as mock_dispatch_avail:
            mock_dispatch_avail.return_value = True
            with patch.object(
                adapter.dispatch_channel, "execute"
            ) as mock_dispatch_exec:
                expected_result = CommandResult(
                    success=True,
                    exit_code=0,
                    stdout="Command executed successfully",
                    stderr="",
                    channel="dispatch",
                    execution_time_ms=250,
                )
                mock_dispatch_exec.return_value = expected_result

                result = await adapter.execute("ls -la /tmp")

                assert result.success is True
                assert result.channel == "dispatch"
                assert "successfully" in result.stdout

    await adapter.close()
