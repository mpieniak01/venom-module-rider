# Coding instructions — Venom Rider-Pi module

## Scope and quality

- Use the existing Venom optional-module contracts and shared services.
- Keep robot-specific logic in this repository.
- Primary manual control must work without chat, STT or an AI model.
- Use one domain service for panel and optional chat tools.
- Rider-Pi is authoritative for calibration, profiles, units, motion sequencing and actuation.
- User-facing text uses module-owned PL, EN and DE translations.
- Prefer cohesive commits and a complete business increment per main PR.
- Do not request repeated bot reviews or publish placeholder PRs.
- Use the existing Venom virtualenv and frontend dependencies; do not install globally.
- Before behavioral delivery, run targeted checks for changed code and the relevant repository gates. Register quality commands with the first functional increment.
- Documentation-only and phase-0 repository setup have no runtime behavior to test.
- Report exact checks, results and remaining limits; never claim physical success from SDK acceptance.

## Hardware and tests

- Default development is isolated from the real robot.
- Automated unit, integration and browser tests use fixtures or a mock.
- No automatic arming, driving, height, balance or actions in tests, deploy, diagnostics or page startup.
- Physical actuator checks require the operator's explicit permission for the specific action.
- Do not clear safety state or change LCD orientation to make a check pass.
- API and emergency CLI must share the hardware domain logic.

## Collaboration

- One business increment at a time.
- At most two supervised workers with disjoint file ownership.
- Contract, safety, dependency and shared-configuration changes have one owner.
- A worker does not publish, merge, deploy or modify credentials.
- The main agent reviews changes and integrates them before delivery.

## Local documentation and secrets

- Public documentation describes supported interfaces and usage.
- Plans and reports remain under ignored docs_dev/.
- Credentials, local environment files and device addresses remain private.
- Never commit or print secrets.
