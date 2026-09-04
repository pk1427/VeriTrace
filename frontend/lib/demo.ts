/** Local-only registry used by the recording flow.
 *
 * Keeping it separate from data/consent_registry.json lets a presenter reset
 * and re-enroll during a demo without changing the team's existing consent
 * records. The file is ignored by git because it contains biometric vectors.
 */
export const DEMO_REGISTRY_RELATIVE_PATH = "data/demo_consent_registry.json";

export const EMPTY_DEMO_REGISTRY = {
  version: "0.2.0",
  match_threshold: 0.6,
  subjects: [],
};
