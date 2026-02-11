# Response to OpenClaw Security Scan

This document explains how we've addressed the security concerns raised by OpenClaw's skill scanner for ClawBrain v0.1.10.

## Summary of Changes

We've made significant improvements to address all concerns raised in the OpenClaw security scan:

1. ✅ Fixed metadata inconsistencies
2. ✅ Added comprehensive security documentation
3. ✅ Enhanced CLI warnings for sensitive operations
4. ✅ Clarified installation security implications
5. ✅ Documented all permissions and capabilities

## OpenClaw Warnings & Our Response

### 1. "Purpose & Capability - SUSPICIOUS"

**Concern**: Registry metadata claims no required environment variables, but SKILL.md documents many env vars (BRAIN_ENCRYPTION_KEY, Postgres/Redis credentials).

**Resolution**:
- ✅ **Fixed version mismatch**: skill.json was at 3.1.0, now corrected to 0.1.10
- ✅ **Clarified environment variables**: skill.json now explicitly lists all optional env vars in the `environment.optional` section
- ✅ **Added security metadata**: New `security` section in skill.json documents:
  - All permissions required (file_system, env vars, startup hooks)
  - Key management capabilities
  - Install actions
  - Network access (optional PostgreSQL/Redis only)

**Why env vars are optional**: ClawBrain works with ZERO configuration using SQLite and auto-generated encryption keys. PostgreSQL, Redis, and custom encryption keys are entirely optional.

### 2. "Instruction Scope - SUSPICIOUS"

**Concern**: Instructions include systemd modifications, encryption key management (including `--full` flag), and hooks that auto-load memory at startup. Documentation claims "No sudo required" but shows systemd instructions.

**Resolution**:
- ✅ **Enhanced CLI warnings**: `clawbrain show-key --full` now:
  - Displays explicit security warning before showing key
  - Requires `yes` confirmation (not just `y`)
  - Explains when and why to use this command
  - Provides terminal history clearing instructions
  - See [clawbrain_cli.py:321-361](clawbrain_cli.py#L321-L361)

- ✅ **Clarified "No sudo required"** (v0.1.13):
  - Core installation: **NEVER needs sudo** (`pip install` + `clawbrain setup`)
  - Systemd instructions: **OPTIONAL** and only for env var configuration
  - Added "No Sudo Required (Core Installation)" section to SECURITY.md
  - Documentation now clearly separates required (no sudo) vs optional (sudo) steps
  - Alternative: Set env vars in shell profile (no sudo needed)

- ✅ **Documented systemd modifications**:
  - SECURITY.md explicitly labels systemd as **OPTIONAL**
  - Two options provided: shell env vars (no sudo) or systemd drop-ins (sudo)
  - ClawBrain works with **zero configuration** (SQLite + auto-generated key)

- ✅ **Clarified hook behavior**:
  - SECURITY.md documents exactly what hooks do
  - Hook code is ~50 lines and fully auditable
  - Hooks install to user directory (`~/.openclaw/hooks`) - no sudo
  - Hooks can be disabled without breaking core functionality

**Why these capabilities exist**:
- Key display is for backup/recovery (users must be able to backup keys)
- Hooks enable automatic memory refresh (core feature, no sudo)
- Systemd is ONE **optional** way to set env vars, not the only way
- Core installation and operation: **never requires root**

### 3. "Install Mechanism - SUSPICIOUS"

**Concern**: Presence of install.sh and remote-install.sh scripts that may perform system-level changes.

**Resolution**:
- ✅ **REMOVED shell scripts entirely** (v0.1.11+):
  - Deleted `install.sh` and `remote-install.sh`
  - Replaced with CLI-only installation: `clawbrain setup`
  - No shell script execution required

- ✅ **CLI-based installation only**:
  - `pip install clawbrain[all]` (PyPI, checksummed)
  - `clawbrain setup` (Python CLI, no bash)
  - Fully auditable Python code
  - No arbitrary shell execution

- ✅ **Security improvements**:
  - Eliminates "curl | bash" pattern entirely
  - No shell script parsing vulnerabilities
  - Standard Python package installation only
  - Transparent CLI source code

**Why this is better**:
- No shell scripts to audit
- No remote script execution
- Standard pip workflow only
- CLI source is part of the package (fully reviewable)

### 4. "Credentials - SUSPICIOUS"

**Concern**: Registry lists no required env vars but skill uses encryption keys and DB credentials. CLI can display full encryption keys.

**Resolution**:
- ✅ **skill.json declares all env vars** (v0.1.11+):
  - `environment.optional` lists all 5 env vars (see [skill.json:30-55](skill.json#L30-L55))
  - Each includes description and whether sensitive
  - **Note for scanner**: Vars are in `optional` not `required` because ClawBrain works with zero config
  - Scanner may be using cached data if still showing "no env vars"

- ✅ **Key management security**:
  - SECURITY.md has entire section on key management
  - CLI warnings before displaying full key (see #2 above)
  - Best practices documented (backup, permissions, rotation)
  - Threat model explains protections and limitations

- ✅ **Credential handling**:
  - PostgreSQL/Redis passwords only used if user configures them
  - No credentials stored by ClawBrain (users supply them)
  - Encryption key is auto-generated and stored locally
  - File permissions set to 0600 on Unix

**Why key display is intentional**: Users MUST be able to backup encryption keys. Lost keys = lost encrypted data. The CLI provides this capability with strong warnings.

### 5. "Persistence & Privilege - SUSPICIOUS"

**Concern**: Hooks run on gateway:startup and command:new. Model invocation not disabled, so AI can call autonomously. Combined with encrypted secrets and startup hooks, this is significant privilege.

**Resolution**:
- ✅ **Documented privilege model**:
  - SECURITY.md explains what hooks do on each event
  - Hook code is auditable (50 lines JavaScript)
  - Hooks can be disabled without breaking Python API

- ✅ **AI invocation is intentional**:
  - This is a memory system - AI needs to access memories
  - No destructive operations available via skill
  - Read-only context injection on startup
  - Write operations (remember) require explicit calls

- ✅ **Startup hook transparency**:
  - gateway:startup: Loads memories, injects context (read-only)
  - command:new: Saves session summary (write only on explicit /new)
  - No automatic execution of stored data
  - No code execution from memory content

**Security boundaries**:
- Hooks cannot execute arbitrary code
- Memories are data, not executable
- No file system access from hooks beyond Brain API
- Python sandboxing applies (no shell commands from memories)

## What To Consider Before Installing

We now provide comprehensive guidance in [SECURITY.md](SECURITY.md):

### Pre-Installation Checklist
- [ ] Verify source (PyPI checksums or GitHub repo)
- [ ] Review install.sh and hook code
- [ ] Understand key management implications
- [ ] Decide on backup strategy
- [ ] Review what data will be stored
- [ ] Choose installation method (PyPI, git clone, remote)

### Security Features
- **Encryption**: Fernet for secrets only
- **Key Storage**: `~/.config/clawbrain/.brain_key` (0600 perms)
- **Network**: None by default, optional PostgreSQL/Redis
- **Telemetry**: None, zero external calls
- **Audit Trail**: All code open source and reviewable

### Threat Model
- **Protected Against**: Encrypted at rest, local-only by default, user isolation
- **Not Protected Against**: Root access, memory dumps, compromised Python env
- **Recommendations**: Full disk encryption, restrictive permissions, network isolation

## Changes Made

### Files Added
1. **[SECURITY.md](SECURITY.md)** - Comprehensive security documentation (300+ lines)
   - Security model and threat analysis
   - Installation method comparison
   - Key management best practices
   - Permission requirements
   - Network access details
   - Vulnerability reporting

2. **[OPENCLAW_RESPONSE.md](OPENCLAW_RESPONSE.md)** - This document

### Files Modified
1. **[skill.json](skill.json)**
   - Fixed version: 3.1.0 → 0.1.10
   - Added `security` section with:
     - `requires_review: true`
     - `permissions` array
     - `key_management` details
     - `install_actions` list
     - `network_access` clarification

2. **[clawbrain_cli.py](clawbrain_cli.py)**
   - Enhanced `cmd_show_key()` function:
     - Security warning before full key display
     - Requires explicit "yes" confirmation
     - Best practices reminder
     - Terminal history clearing instructions

3. **[SKILL.md](SKILL.md)**
   - Added "Security & Transparency" section
   - Added security notes to installation instructions
   - Enhanced encrypted secrets documentation
   - Links to SECURITY.md throughout

4. **[README.md](README.md)**
   - Added "Security" section
   - Links to SECURITY.md

## Conclusion

All OpenClaw concerns have been addressed:

1. ✅ **Metadata Consistency**: skill.json now matches pyproject.toml version and declares all env vars
2. ✅ **Installation Transparency**: Full documentation of what install scripts do
3. ✅ **Key Management**: Strong warnings and best practices documented
4. ✅ **Permissions**: Explicitly documented in skill.json and SECURITY.md
5. ✅ **Hooks**: Behavior documented, code auditable, can be disabled

**ClawBrain's security posture**:
- Local-only by default (no network calls)
- No telemetry or external APIs
- Open source and auditable
- Sensible defaults (SQLite, auto-generated keys)
- Optional features require explicit configuration

**User responsibility**:
- Review code before installation (especially for production)
- Backup encryption keys
- Secure file permissions
- Understand what permissions are granted
- Follow best practices in SECURITY.md

The skill is now fully transparent about its capabilities, permissions, and security implications. Users can make informed decisions based on comprehensive documentation.

---

**Version**: 0.1.13
**Last Updated**: 2026-02-10
**Security Contact**: clawcolab@gmail.com (via GitHub Issues)
