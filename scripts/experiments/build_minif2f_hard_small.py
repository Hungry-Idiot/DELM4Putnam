#!/usr/bin/env python3
"""Build the internal miniF2F-hard-small benchmark files."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEAN_PATH = ROOT / "data/PutnamBench/lean4/src/minif2f_hard_small_batch.lean"
PROBLEMS_PATH = ROOT / "runs/minif2f_hard_small/problems.txt"


PROBLEMS = [
    "minif2f_hard_small_amc12a_2019_p9",
    "minif2f_hard_small_amc12a_2015_p10",
    "minif2f_hard_small_amc12a_2009_p9",
    "minif2f_hard_small_amc12_2001_p9",
    "minif2f_hard_small_mathd_numbertheory_13",
    "minif2f_hard_small_mathd_numbertheory_780",
    "minif2f_hard_small_mathd_algebra_73",
    "minif2f_hard_small_mathd_algebra_140",
]


LEAN_CONTENT = """import Mathlib
import Aesop

set_option maxHeartbeats 0

open BigOperators Real Nat Topology Rat
open scoped BigOperators

-- Internal miniF2F-hard-small benchmark.
-- Theorems are selected from existing miniF2F/math competition style Lean statements.

-- source_name: amc12a_2019_p9
-- selection_reason: rational recurrence with numerator/denominator normalization, harder than linear medium items
theorem minif2f_hard_small_amc12a_2019_p9 (a : ℕ → ℚ) (h₀ : a 1 = 1) (h₁ : a 2 = 3 / 7)
  (h₂ : ∀ n, a (n + 2) = a n * a (n + 1) / (2 * a n - a (n + 1))) :
  ↑(a 2019).den + (a 2019).num = 8078 :=
sorry

-- source_name: amc12a_2015_p10
-- selection_reason: integer factorization with inequalities and uniqueness constraints
theorem minif2f_hard_small_amc12a_2015_p10 (x y : ℤ) (h₀ : 0 < y) (h₁ : y < x) (h₂ : x + y + x * y = 80) : x = 26 :=
sorry

-- source_name: amc12a_2009_p9
-- selection_reason: polynomial identity under shifted input, requiring coefficient recovery
theorem minif2f_hard_small_amc12a_2009_p9 (a b c : ℝ) (f : ℝ → ℝ) (h₀ : ∀ x, f (x + 3) = 3 * x ^ 2 + 7 * x + 4)
  (h₁ : ∀ x, f x = a * x ^ 2 + b * x + c) : a + b + c = 2 :=
sorry

-- source_name: amc12_2001_p9
-- selection_reason: quantified functional equation over positive reals
theorem minif2f_hard_small_amc12_2001_p9 (f : ℝ → ℝ) (h₀ : ∀ x > 0, ∀ y > 0, f (x * y) = f x / y) (h₁ : f 500 = 3) :
    f 600 = 5 / 2 :=
sorry

-- source_name: mathd_numbertheory_13
-- selection_reason: congruence solution set with IsLeast constraints and rational average
theorem minif2f_hard_small_mathd_numbertheory_13 (u v : ℕ) (S : Set ℕ)
  (h₀ : ∀ n : ℕ, n ∈ S ↔ 0 < n ∧ 14 * n % 100 = 46) (h₁ : IsLeast S u)
  (h₂ : IsLeast (S \\ {u}) v) : (u + v : ℚ) / 2 = 64 :=
sorry

-- source_name: mathd_numbertheory_780
-- selection_reason: modular inverse style constraints over integers
theorem minif2f_hard_small_mathd_numbertheory_780 (m x : ℤ) (h₀ : 0 ≤ x) (h₁ : 10 ≤ m ∧ m ≤ 99) (h₂ : 6 * x % m = 1)
  (h₃ : (x - 6 ^ 2) % m = 0) : m = 43 :=
sorry

-- source_name: mathd_algebra_73
-- selection_reason: complex quadratic equation root relation with exclusion hypothesis
theorem minif2f_hard_small_mathd_algebra_73 (p q r x : ℂ) (h₀ : (x - p) * (x - q) = (r - p) * (r - q)) (h₁ : x ≠ r) :
  x = p + q - r :=
sorry

-- source_name: mathd_algebra_140
-- selection_reason: factorization identity with positive parameters and coefficient constraints
theorem minif2f_hard_small_mathd_algebra_140 (a b c : ℝ) (h₀ : 0 < a ∧ 0 < b ∧ 0 < c)
  (h₁ : ∀ x, 24 * x ^ 2 - 19 * x - 35 = (a * x - 5) * (2 * (b * x) + c)) : a * b - 3 * c = -9 :=
sorry
"""


def main() -> None:
    LEAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBLEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEAN_PATH.write_text(LEAN_CONTENT, encoding="utf-8")
    PROBLEMS_PATH.write_text("\n".join(PROBLEMS) + "\n", encoding="utf-8")
    print(f"Wrote {LEAN_PATH}")
    print(f"Wrote {PROBLEMS_PATH}")


if __name__ == "__main__":
    main()
