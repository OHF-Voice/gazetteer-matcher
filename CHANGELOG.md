# Changelog

## 1.1.0

- Resolve `them` against every target the previous turn named, instead of
  refusing a follow-up after a coordinated command. The reading is settled
  against the first target and its intent applied to the rest, so targets of
  differing scope are reached together, and a target the action cannot apply
  to rejects the sentence rather than acting on only some of them.
- `it` after a turn that named more than one target is still refused, and
  still reports `anaphora_multiple_targets`.
- Add `FrameCandidate.anaphor_target`, recording which supplied antecedent a
  reading resolved against.

## 1.0.0

- Initial release
