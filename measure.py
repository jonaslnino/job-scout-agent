"""
Is the agent showing you the right jobs? Compares its recommendations with your verdicts.

1. Open output/tracker.csv and fill `my_verdict` for the jobs you reviewed: High, Medium or Pass.
2. Run: python measure.py

It reports two numbers, the same ones used to tune this agent while building it:
- Catch rate: of the jobs you wanted (High/Medium), how many the agent recommended High or Medium.
  This is the number that matters: a missed job you'd want is the costly mistake.
- Noise: of the jobs you passed on (Pass), how many the agent recommended High.

When you change profile/criteria.md, bump its version and compare runs by `criteria_version`.
Measure a new version on jobs you did NOT use to write it; otherwise it just memorizes.
"""

from collections import defaultdict

import output

WANTED = {"high", "medium", "yes", "maybe", "alta", "media"}
PASSED = {"pass", "no", "descartado"}


def main() -> None:
    rows = [r for r in output.read_tracker() if (r.get("my_verdict") or "").strip()]
    if not rows:
        print("No verdicts yet. Fill `my_verdict` (High / Medium / Pass) in output/tracker.csv first.")
        return
    by_version = defaultdict(list)
    for r in rows:
        by_version[r.get("criteria_version") or "?"].append(r)

    print(f"{'criteria':<10}{'judged':>8}{'catch rate':>14}{'noise':>10}")
    for version, rs in sorted(by_version.items()):
        wanted = [r for r in rs if r["my_verdict"].strip().lower() in WANTED]
        passed = [r for r in rs if r["my_verdict"].strip().lower() in PASSED]
        caught = sum(r.get("recommendation") in ("High", "Medium") for r in wanted)
        noisy = sum(r.get("recommendation") == "High" for r in passed)
        catch = f"{caught}/{len(wanted)}" if wanted else "-"
        noise = f"{noisy}/{len(passed)}" if passed else "-"
        print(f"{version:<10}{len(rs):>8}{catch:>14}{noise:>10}")

    print("\nOnly shown jobs are in the tracker. To check for misses, skim the 'Hidden' section of each "
          "output/report-*.md: a hidden job you would have wanted is a miss.")


if __name__ == "__main__":
    main()
