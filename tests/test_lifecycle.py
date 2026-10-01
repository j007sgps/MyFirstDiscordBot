import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class LifecycleTests(unittest.TestCase):
    def test_setup_reload_and_shutdown_have_no_duplicate_commands_or_live_clients(self):
        # Reload replaces module objects; run this in a child process so mocks in
        # other test modules cannot retain stale module globals.
        script = textwrap.dedent("""
            import asyncio
            import os
            import sys
            from pathlib import Path
            from unittest.mock import AsyncMock, patch
            import discord
            import config

            async def main():
                root = Path(sys.argv[1])
                with patch.object(config, 'CHAT_DB_PATH', root / 'chat.db'), patch.object(config, 'BOT_STATE_DB_PATH', root / 'state.db'), patch.dict(os.environ, {'GEMINI_API_KEY': ''}):
                    from bot import VibeBot
                    bot = VibeBot(command_prefix='!', intents=discord.Intents.none())
                    async with bot:
                        bot.tree.sync = AsyncMock(return_value=[])
                        with patch('builtins.print'):
                            await bot.setup_hook()
                        assert set(bot.extensions) == {'cogs.youtube', 'cogs.ai_chat', 'cogs.admin', 'cogs.community'}
                        names = {command.name for command in bot.tree.get_commands()}
                        assert len(names) == 18
                        old_youtube = bot.get_cog('YouTubeTracker')
                        old_session = old_youtube.session
                        await bot.reload_extension('cogs.youtube')
                        assert old_session.closed
                        assert not old_youtube.check_new_video.is_running()
                        await bot.reload_extension('cogs.ai_chat')
                        await bot.reload_extension('cogs.admin')
                        old_community = bot.get_cog('Community')
                        await bot.reload_extension('cogs.community')
                        assert not old_community.worker.is_running()
                        assert names == {command.name for command in bot.tree.get_commands()}
                        new_session = bot.get_cog('YouTubeTracker').session
                        community = bot.get_cog('Community')
                    assert new_session.closed
                    assert not community.worker.is_running()
                print('lifecycle OK')
            asyncio.run(main())
        """)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', script, directory], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lifecycle OK', result.stdout)
