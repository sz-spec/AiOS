# musl 1.2.6 upstream merge — 2026-09-13

Updated vendored upstream sources from 1.2.5 to 1.2.6. Official maintainer
[release announcement](https://www.openwall.com/lists/musl/2026/03/20/1)
identifies the iconv CVE-2025-26519 correction, semaphore wakeup fixes, and
changed pwrite behavior among this release's changes. This merge includes the
upstream corrected iconv.c bytes; no claim is made that all VOS ABI behavior
or kernel security has thereby been proven.

## Provenance and merge method

Downloaded both official HTTPS release archives, extracted into disposable
temporary directories, and compared each existing upstream file byte-for-byte
against official 1.2.5. There were **zero locally modified upstream files**.
The VOS changes are additional files, so the three-way merge reduces to
advancing unchanged base files while retaining additions and local omissions.
No conflicting edits required manual resolution.

| Archive | SHA256 |
| --- | --- |
| musl-1.2.5.tar.gz | a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4 |
| musl-1.2.6.tar.gz | d585fd3b613c66151fc3249e8ed44f77020cb5e6c1e635a616d3f9f82460512a |

URLs, full per-file delta and preservation hashes are recorded in
`dependencies/musl-upgrade.json`. Detached signature verification was not
performed; these are recorded download hashes, not independently authenticated
release signatures.

133 upstream files changed, 14 were added, and 51 were removed according to
upstream 1.2.6. Removed architecture headers are upstream consolidations into
generic headers. The existing omission of dist/config.mak was preserved.
Seven tracked VOS-added files were preserved with identical SHA256 hashes:
the two include/bits headers, three ldso linker/stub files, vos3_stubs.c and
vos3_syscall_cp.s. Makefile.vos3 was excluded from this agent's writes because
the integration agent owns its concurrent build dependency corrections.

## Validation boundaries and integration handoff

Byte identity checks prove preservation of the seven port additions, not their
compatibility with every new upstream path. Native compilation and boot tests
are delegated to the build agent after the source merge completion signal;
their results must be recorded separately. No concurrent full native build was
started by this agent.

Upstream pwrite now tries pwritev2 with RWF_NOAPPEND and falls back on ENOSYS or
EOPNOTSUPP; append-mode fallback explicitly returns EOPNOTSUPP. VOS syscall
compatibility, errno behavior and append semantics need runtime coverage. New
posix_getdents and Linux-specific additions must not be advertised as supported
VOS APIs merely because their source exists. Similarly, importing the iconv
fix does not replace a regression test of invalid encoding inputs against the
VOS-built libc. Existing bounded Z3 models concern their modeled properties;
they do not establish security of this ABI or the actual kernel.
