# `review/1`

What `code-review` writes for a pull request. It is rendered as one consolidated comment, and is
optionally posted with inline comments for findings that carry a suggestion.

There is **no verdict and no counts**. The renderer derives the disposition ("Request changes"
if any finding is critical or important) and the per-severity counts from the findings. A value
the model writes twice can disagree with itself.

Validated against this diff:

```diff
diff --git a/shop/views.py b/shop/views.py
--- a/shop/views.py
+++ b/shop/views.py
@@ -88,4 +88,4 @@ def cancel_order(request, number):
     if request.method != 'POST':
         return HttpResponseNotAllowed(['POST'])
-    order = get_object_or_404(Order, number=number, customer=request.user)
+    order = Order.objects.get(number=number)
     order.cancel()
```

```json
{
  "schema": "review/1",
  "project": "example-org/example-app",
  "produced_by": {"skill": "code-review", "automation_sha": "889bd9b0000000000000000000000000000000aa", "model": "claude-opus-5-5"},
  "run": "20261111-093000-code-review",
  "pr": 42,
  "base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "2222222222222222222222222222222222222222",
  "followup_of": null,
  "summary": "Checked the view change against the URLconf, the Order model and the cancel tests.",
  "findings": [
    {
      "id": "F1",
      "fingerprint": "shop/cancel-any-customers-order",
      "severity": "critical",
      "category": "security",
      "rule": null,
      "title": "Any signed-in user can now cancel anyone's order",
      "body": "The lookup no longer filters by customer, so a user who guesses an order number can cancel it. An unknown number also raises DoesNotExist, a 500, where it was a 404.",
      "evidence": "verified",
      "location": {"path": "shop/views.py", "line": 90, "side": "RIGHT", "in_diff": true},
      "suggestion": "    order = get_object_or_404(Order, number=number, customer=request.user)",
      "status": "new"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `run` | The id of the run that wrote the review. Its `run/1` record is embedded beside it in the posted comment. |
| `pr`, `base_sha`, `head_sha` | The PR and the exact commits reviewed. A review is bound to `head_sha`. |
| `followup_of` | The `head_sha` of the previous review of this PR, or `null`. |
| `summary` | What was checked, and anything deliberately out of scope. Not a restatement of the diff. |
| `findings[].id` | `F1`, `F2`, … within this review. |
| `findings[].fingerprint` | `area/slug`, **stable across reviews of the PR**: a follow-up review reuses it to report what happened to the finding. |
| `severity` | `critical`, `important`, `suggestion`, or `nit`. A nit must have a suggestion. |
| `category` | The knowledge area, e.g. a review playbook name: `correctness`, `migrations`, `querysets`. |
| `rule` | The rule the finding applies, if one is documented (e.g. `migrations/field-parameter-drift`), else `null`. A finding that cites a rule is an enforcement gap, not a new rule. |
| `evidence` | `verified` or `inferred`. |
| `location` | `null` for a finding about the PR as a whole. Otherwise `path`, `line`, `side` and `in_diff`, plus `start_line` and `start_side` for a range of at most 10 lines. |
| `suggestion` | Replacement text for exactly `start_line`..`line`, with no fence (the renderer adds it). It needs `in_diff: true`. |
| `status` | `new`, or for a follow-up: `still_open`, `addressed`, `declined` or `withdrawn`. |

## Locations

Take coordinates from the annotated diff (`bin/artifact annotate-diff`):

| Annotation | `side` | `line` |
|---|---|---|
| `[OLD:n]` removed | `LEFT` | `n` |
| `[NEW:m]` added | `RIGHT` | `m` |
| `[OLD:n,NEW:m]` context | `RIGHT` | `m` |

The validator checks `in_diff` against the diff in both directions:

- `true` on a line the diff doesn't show is an error, because GitHub would reject the review.
- `false` on a line it does show is also an error, so the field stays a fact.

A finding with `in_diff: false` is rendered in the comment body with its `path:line`.

A suggestion that begins with the line just above the range, or ends with the line just below
it, is rejected, because applying it would duplicate that line.
