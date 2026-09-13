# VOS3 Build Signing

## Status: REQUIRES GPG SETUP

GPG is not installed on the current build machine. Build signing requires
manual setup before detached signatures can be generated.

## Current Integrity Verification

The gold master binary is verified by SHA-256 hash:

```
Binary:  kernel/build/vos3.elf
SHA-256: 196de8e59a3557bfe4202932b54641d7694b44117e7a0afabf9ddb120b137e30
```

## Setup Instructions

### 1. Install GPG

```bash
# macOS
brew install gnupg

# Linux (Debian/Ubuntu)
apt install gnupg
```

### 2. Generate a Signing Key

```bash
gpg --full-generate-key
# Select: RSA and RSA, 4096 bits, no expiration (or 2y)
# Name: VOS3 Build Signing
# Email: builds@vos3.dev
```

### 3. Sign the Kernel Binary

```bash
gpg --detach-sign --armor kernel/build/vos3.elf
# Output: kernel/build/vos3.elf.asc
```

### 4. Export the Public Key

```bash
gpg --export --armor "VOS3 Build Signing" > docs/vos3-signing-key.pub
```

### 5. Verify a Signed Binary

```bash
gpg --verify kernel/build/vos3.elf.asc kernel/build/vos3.elf
```

## Alternative: cosign (Sigstore)

For keyless signing with transparency log:

```bash
# Install
brew install sigstore/tap/cosign

# Sign (opens browser for OIDC auth)
cosign sign-blob --output-signature kernel/build/vos3.elf.sig \
                 --output-certificate kernel/build/vos3.elf.cert \
                 kernel/build/vos3.elf

# Verify
cosign verify-blob --signature kernel/build/vos3.elf.sig \
                   --certificate kernel/build/vos3.elf.cert \
                   --certificate-identity builds@vos3.dev \
                   --certificate-oidc-issuer https://accounts.google.com \
                   kernel/build/vos3.elf
```

## CI Integration

Once GPG or cosign is configured, add to the build pipeline:

```bash
# In Makefile or CI script, after `make`:
gpg --detach-sign --armor build/vos3.elf
sha256sum build/vos3.elf > build/vos3.elf.sha256
```
