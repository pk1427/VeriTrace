# VeriTrace

**Consent-gated face verification + blockchain pipeline** — HH Goa 2026 hackathon submission (Task 3).

A CLI that gates every face search/match behind an explicit owner **consent
registry**, then feeds tamper-evident evidence onto a blockchain for
independent re-verification.

---

## Non-negotiable design constraint

> The pipeline **never** searches for or matches a face that has not been
> explicitly enrolled by its owner in the local consent registry. This is
> enforced in code as a hard gate that runs **before** any search API call.
> If no matching enrolled face is found, the program refuses to proceed and
> prints a clear refusal message.

Phase 1 (this checkpoint) performs **local detection + embedding only** — there
is no web search path at all yet, so the constraint is trivially satisfied.
Phase 2 adds the consent gate; later phases wire in the live search.

---

## Phase 1 status (face in -> embedding out)

CLI:

```bash
python src/main.py scan data/input/face.jpg
```

This detects the largest face, prints its bounding box, and generates a
**512-dim ArcFace (L2-normalized)** embedding. Nothing sensitive is persisted.

### Backends

| Layer         | Primary                         | Fallback                  |
|---------------|---------------------------------|---------------------------|
| Detection     | InsightFace SCRFD (CPU)         | face_recognition / dlib   |
| Embedding     | InsightFace ArcFace r100 (512-d)| face_recognition (128-d)  |
| Similarity    | NumPy cosine similarity         | —                         |

### Layout

```
veritrace/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── data/
│   ├── consent_registry.json   # Phase 2 (empty placeholder now)
│   ├── input/
│   └── evidence/               # Phase 3
├── src/
│   ├── main.py                 # CLI (Phase 1: `scan`)
│   ├── face/{
│   │   ├── __init__.py         # public API re-exports
│   │   ├── _engine.py          # shared lazy backend (InsightFace/fallback)
│   │   ├── detector.py         # bounding-box detection
│   │   ├── encoder.py          # 512-dim embedding (+ bbox-scoped encoding)
│   │   └── matcher.py          # cosine similarity + match()
│   ├── consent/registry.py     # Phase 2 stub (not wired in yet)
│   ├── search/                 # Phase 2
│   ├── evidence/               # Phase 3
│   └── blockchain/             # Phase 3
├── test/
│   ├── test_matcher.py         # synthetic >0.6 / <0.4 gate math (runs offline)
│   ├── test_phase1_e2e.py      # real-image e2e thresholds (skipped if no images)
│   └── samples/                # same_a/same_b/diff_a/diff_b + README
├── contracts/EvidenceRegistry.sol
├── scripts/deploy.js
└── test/EvidenceRegistry.test.js
```

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# copy secrets (never commit the real file)
cp .env.example .env
```

> **Python**: 3.10+ recommended. This repo also imports cleanly on 3.9; if you
> need a newer interpreter install via `pyenv`/`brew`/python.org and recreate
> the venv.

> **Model pack on first run**: `scan` (and the e2e test) lazily download the
> InsightFace `buffalo_sc` pack (~15 MB) on first use — a MobileFaceNet ArcFace
> recognizer (`w600k_mbf`, **512-dim** output) + RetinaFace detector — cached at
> `~/.insightface/`. For higher accuracy swap to `buffalo_l` by setting
> `VERIFACE_MODEL_PACK=buffalo_l` in `.env` (downloads ~275 MB, ResNet-100).
> The stock `insightface==0.7.3` default pack name `antelope` 404s on the v0.7
> GitHub release (renamed to `antelopev2`); this repo defaults to `buffalo_sc`.

> **macOS note**: InsightFace ships pure-wheel CPU inference (`onnxruntime`),
> so the primary stack installs without a C++ toolchain. The `face_recognition`
> fallback (in `requirements.txt`, commented) requires `dlib`, which compiles
> from source — use it only if InsightFace can't install on your machine.

---

## Run

```bash
python src/main.py scan data/input/face.jpg
# -> prints backend, bbox (x1,y1)->(x2,y2), embedding dim + norm; saves nothing
```

On an image with **no detectable face**, `scan` refuses locally:

```
refusal: no face detected in the supplied image — nothing to embed.
```

(Phase 1 has no web search, so there is no unenrolled-face search path to gate
yet; the consent gate lands in Phase 2, before any search API is wired up.)

### Phase 2 — consent gate

Subjects are enrolled into `data/consent_registry.json` as a `subjects` list of
`{subject_id, embedding_b64, ...}` records (the biometric vector is never logged).
The live registry is git-ignored; the repo ships `data/consent_registry.example.json`.

```bash
# Enroll an owner-identified subject (detects the first face in the image):
python src/main.py enroll prasad data/input/prasad.jpg          # subject_id is required, explicit
# Re-enrolling the same subject_id is refused (no silent overwrite).

python src/main.py check  data/input/face.jpg                   # fail-closed at 0.60
# -> consented: prasad        (cosine match in registry >= 0.60)
# -> refused: <score> < 0.60  (below gate)
```

The gate is **fail-closed**: if the registry is empty, no face is detected, or the
best match is below `0.60`, the face is refused. `scan` remains a Phase-1 tool
(embedding only) and does not enroll or check consent.

```bash
# Phase 2 consent-gate tests (isolated temp registries, never touch the real one):
python test/test_phase2_consent.py
```

`test/test_phase2_consent.py` is **real-face-aware**: it auto-discovers sample photos
in `test/samples/`. See "Test fixtures" for behavior when samples are absent
(tests are skipped, not failed).

### Phase 3 — evidence + chain

Evidence is produced and anchored behind the **consent gate**: `check` must be
OPEN for the face before any fingerprint is taken or any on-chain call is made.

```bash
# 1) consent-gate an image (must be OPEN):
python src/main.py check data/input/face.jpg

# 2) fingerprint -> tamper-evident SHA-256 evidence JSON (data/evidence/ git-ignored):
python src/main.py evidence data/input/face.jpg

# 3) anchor the fingerprint on EvidenceRegistry.sol:
#    --key is optional: omit it and the CLI writes an unsigned
#    data/evidence/anchor_request.json for an external signer; supply
#    --key <hex> to sign + broadcast directly.
python src/main.py blockchain anchor data/input/face.jpg --key <owner-privkey> --rpc <rpc> --contract <addr>

# 4) independently re-verify on-chain (read-only eth_call):
python src/main.py blockchain verify data/input/face.jpg --rpc <rpc> --contract <addr>

# 5) deploy the contract:
npx hardhat run scripts/deploy.js --network localhost  # local dev node, no secrets
npx hardhat run scripts/deploy.js --network amoy       # needs POLYGON_AMOY_RPC_URL + PRIVATE_KEY (.env, git-ignored)
```

The evidence fingerprint is `SHA-256(image bytes)`. Any byte change produces a
different hash that the on-chain record no longer matches — tampering is
detectable by re-hashing off-chain and calling `isRecorded(hash)`.

---

## Tests

```bash
# Matcher math only — no model, no images, always runs (NumPy):
python test/test_matcher.py

# Full pipeline on real faces — supply samples/test/samples/*.jpg first:
python test/test_phase1_e2e.py

# Phase 2 consent gate — auto-discovers real face samples; isolated temp
# registries. Skipped (not failed) if samples are absent:
python test/test_phase2_consent.py

# Phase 3 evidence + blockchain clients (offline, no node needed):
python test/test_evidence.py
python test/test_blockchain_client.py

# Phase 3 contract (JS — requires `npm install` first):
npx hardhat compile
npx hardhat test
```

### Test fixtures

`test/test_matcher.py` uses **synthetic** embeddings to assert the gate math:
same-identity pair -> cosine > 0.6, different-identity pair -> cosine < 0.4.
It runs offline with only NumPy.

`test/test_phase1_e2e.py` exercises detection -> embedding -> cosine on **real**
photos. Place four face images in `test/samples/` (see `test/samples/README.md`):
`same_a.jpg`, `same_b.jpg` (one person) and `diff_a.jpg`, `diff_b.jpg`
(two people). The test is skipped (not failed) until you supply them.

`test/test_phase2_consent.py` is **real-face-aware**: it auto-discovers a small
set of sample photos in `test/samples/` (any of the conventional or descriptive
names — `your_photo1.jpeg`, `your_photo2.jpeg`, `teammate_photo.jpeg`,
`same_a`/`same_b`/`diff_a`/`diff_b`). When present it enrolls one subject and
asserts the gate **grants** the same person (>= 0.60) and **refuses** a
different person (< 0.60). When a sample set is missing, the affected test is
**skipped**, not failed. All enrollments use an **isolated temporary registry**
under the system temp dir — `data/consent_registry.json` is never read or
written. The empty-registry test additionally uses a synthetic black placeholder
to verify a face is **never embedded** when nobody is enrolled (fail closed).

`test/test_evidence.py` covers the pure-stdlib SHA-256 fingerprinting, payload
hashing, and the tamper demo (flip a byte → hash changes). No model, no network,
always runs offline.

`test/test_blockchain_client.py` covers calldata encoding (selectors + args),
offline transaction signing/recovery, the `eth_call` request shape, and the
unsigned-anchor path. It monkey-patches the JSON-RPC layer, so it runs
offline with no node. The full on-chain round-trip (sign → mine → verify) is
exercised by `test/EvidenceRegistry.test.js` under Hardhat instead.

---

## Phases (in order, each confirmed before building)

1. **Face scan** — detect + 512-dim embedding (✅ checkpoint).
2. **Consent gate + live search** — enroll subjects, fail-closed refusal below 0.60 cosine (✅
   consent gate live & tested; web reverse-image search **deferred** — Phase 2 layer not built).
3. **Evidence + chain** — SHA-256 fingerprint, Solidity Hardhat contract on Polygon Amoy, on-chain
   upload + independent re-verification, tamper demo
   (✅ **locally verified end-to-end** on a Hardhat node: a real signed `addRecord`
   transaction was mined, `verify`/`isRecorded` `eth_call` returns `RECORDED`, and a
   byte-flip tamper produces a different fingerprint that is `UNRECORDED`; **Amoy
   deployment is opt-in** — it requires a funded `POLYGON_AMOY_RPC_URL` + `PRIVATE_KEY`
   in the git-ignored `.env` and an explicit `veritrace blockchain deploy --network amoy`).
