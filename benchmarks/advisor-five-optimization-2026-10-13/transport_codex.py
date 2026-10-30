"""Official binary staging only. The inherited Harbor Codex.run is unmodified."""
from pathlib import Path
from harbor.agents.installed.codex import Codex
class StagedCodex(Codex):
    async def install(self, environment):
        if await self._installed_codex_satisfies_version(environment):return
        await environment.upload_file(Path('/private/tmp/luna-tb4-runtime/codex-linux.tgz'),'/tmp/luna-codex-runtime.tgz')
        await self.exec_as_root(environment,command='mkdir -p /usr/local/bin && tar -xzf /tmp/luna-codex-runtime.tgz -C /usr/local/bin --strip-components=4 package/vendor/x86_64-unknown-linux-musl/bin/codex package/vendor/x86_64-unknown-linux-musl/codex-path/rg && chmod 755 /usr/local/bin/codex /usr/local/bin/rg && rm /tmp/luna-codex-runtime.tgz && /usr/local/bin/codex --version')
        if not await self._installed_codex_satisfies_version(environment):raise RuntimeError('Staged Codex version mismatch')
