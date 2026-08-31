# VeriTrace Phase 1 end-to-end samples

This directory holds the face images used by `test/test_phase1_e2e.py` to
validate the real same-person (>0.6) and different-person (<0.4) gates.

## What to supply

Place four JPEG/PNG photos here (use clear frontal portraits — faces filling
a good portion of the frame, similar lighting/angle between the same-person
pair):

| file        | description                                  |
|-------------|----------------------------------------------|
| `same_a.jpg` | Photo 1 of person A                          |
| `same_b.jpg` | Photo 2 of the **same** person A             |
| `diff_a.jpg` | Photo 1 of a **different** person B          |
| `diff_b.jpg` | Photo 2 of person B (or another person)      |

Then run:

```bash
python test/test_phase1_e2e.py
```

Example: two selfies of yourself from different angles -> `same_a.jpg`,
`same_b.jpg`. A selfie of a friend -> `diff_a.jpg` / `diff_b.jpg`.

## Notes
- Names must match exactly (`same_a.jpg`, etc.).
- The InsightFace ONNX model (~55 MB for the `antelope` pack) downloads
  automatically on first run to `~/.insightface/`.
- If you don't have face images yet, just delete this directory's placeholder
  and re-run later — the test is skipped (not failed) when images are absent.
