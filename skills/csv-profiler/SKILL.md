---
name: csv-profiler
description: Profile a CSV file with the Python standard library only — row and column counts, per-column type inference, null counts, and basic numeric statistics. Use when someone attaches or points at a CSV and wants to know what is in it before analysing it, or when pandas is unavailable or too heavy for the question.
---

# CSV Profiler

Answers "what is actually in this file?" before anyone writes an analysis
against it. Standard library only, so it runs in any sandbox — including one
built from a slimmer image than this bundle's.

## When to use it

- A CSV arrives and you need shape, types, and null density before deciding
  anything.
- A pandas read is failing and you need to know whether the file is the problem.
- The question is small enough that importing pandas costs more than it saves.

## Use it

```sh
python3 skills/csv-profiler/scripts/profile_csv.py <path-to-csv> [--sample 5]
```

It prints a per-column table: inferred type (integer / float / date / boolean /
string), null count and percentage, distinct count, and for numeric columns
min / max / mean / median. `--sample N` adds the first N rows.

## Then

1. **Read the null column first.** A column that is 60% empty is a different
   analysis than one that is 2% empty, and it is the most common reason a
   later aggregate is quietly wrong.
2. **Check the inferred types against what the column is called.** An `amount`
   column inferred as `string` means the file has currency symbols, thousands
   separators, or a stray header row inside the data.
3. **Say the shape out loud** in your answer — row count, column count, the
   columns you are going to use — before you compute anything on it.

Do not report a statistic the profiler did not produce. If the question needs
something beyond the profile, load the file properly with `run_python` and
compute it; the profiler is a first look, not the analysis.

## If the script cannot be read

On the Kubernetes path this file and the script reach the sandbox through the
skills tree fleet stages into the workspace claim at boot. If
`python3 skills/csv-profiler/scripts/profile_csv.py` reports the file does not
exist, do not reimplement it inline and do not guess at its output — say the
skill's script is not available in this deployment and compute what you need
directly with `run_python`. (The operator-side cause is a failed staging step;
the README's "Skills on Kubernetes" section explains it.)
