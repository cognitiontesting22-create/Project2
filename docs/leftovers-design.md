# Leftovers: cook once, plan the portions

This document proposes the full leftovers workflow. The implementation decisions for each stage come from the feature session; the remaining proposal is not a specification for unfinished stages.

## Implemented first stage

Settings stores **Household portions**, a whole number from **1 to 20**, defaulting to **2**. A cooking plan keeps its batch size in `servings` and stores `portions_eaten` separately. Leaving portions eaten blank uses household portions capped at the cooking quantity.

Saving a cooking plan fills only the next calendar day's empty slot for the same meal type, and only if the spare portions cover the whole household. The new `MealPlan` links back through `leftover_of`; its servings and portions eaten both equal the household portions. This never chains to another day automatically. Repeating the same plan preserves its existing leftover meal.

For a household of 2, Monday dinner with 4 servings and blank portions eaten becomes Monday eating 2 and Tuesday's **Expected leftovers** for 2. Six servings also fill Tuesday only; 2 servings create no follow-on meal. Expected leftovers link to the source card, cannot be recorded as cooking batches, and can be removed or replaced with a fresh plan. Changing an uncooked source reconciles its expected follow-on meal; removing the source removes that expected meal.

This stage adds planner scheduling only. Prepared stock, consumption receipts, and changes to pantry or shopping accounting remain future work.

## Implemented second stage

Recording a cooked meal creates one `PreparedBatch` alongside its `CookingRecord`, in the same transaction as the existing raw pantry deductions. The cooking receipt remains the only source of truth for those deductions. The batch snapshots the recipe title and stores actual yield, portions eaten, remaining portions, fridge/freezer location, and an explicitly entered use-by date.

The cook form prefills portions eaten from the plan, or from household portions capped at the yield for an unplanned recipe. Actual portions eaten may be **0** through the yield. Keeping any portions requires a use-by date on or after the cooking date; there is no default shelf life. With nothing left, a zero-remaining batch is still created and the use-by input is ignored. Planned yield and portions eaten update to the recorded values.

Token retries and duplicate recordings of a planned meal preserve the original receipt and batch. Undo restores raw stock from the receipt and removes the batch; a fresh recording after undo creates a new batch. Existing historical receipts are preserved without inventing their missing portion numbers or storage dates.

History displays the batch's made/eaten/remaining amounts and explicit date. A cooked source's follow-on card changes from **Expected leftovers** to **Leftovers ready**; undo changes it back. Consuming prepared portions and changing shopping accounting remain later stages.

## Remaining proposal

The agreed policy is to fill **empty matching meal slots only**, leaving existing plans intact. Household portions should be configurable, with a suggested starting default of **2** until the household size is supplied.

## Expected behavior

Planning a dinner batch for 4 servings in a household eating 2 portions reserves the remaining 2 for the next day's dinner if that slot is empty. Tomorrow's card says **Leftovers from Monday's dinner · 2 portions**, links to the original batch, and has no additional ingredient requirements. The shopping list buys the ingredients for the original 4-serving batch once.

Before cooking, the follow-on meal is marked **Expected leftovers**. Recording the original meal confirms the actual yield and portions eaten. Only then are real prepared portions available. Eating leftovers consumes prepared portions; it does not deduct raw pantry ingredients again.

## Accounting

- Keep planned cooking batches separate from meals eating their portions. Existing `MealPlan.servings` describes the cooking quantity; add an explicit number of portions eaten at that meal.
- Give follow-on meals a reference to the original batch and a reserved portion count. Ingredient requirements come only from cooking batches, never from their leftover reservations.
- When cooking is recorded, create a prepared-meal stock entry with the actual yield, portions eaten, remaining portions, storage location, and an explicit use-by date. A cooking receipt remains the source of raw-ingredient deductions.
- Distinguish **remaining**, **reserved**, and **unassigned** portions so one portion cannot be promised to two meals. Eating a reservation reduces remaining and reserved portions atomically; removing a future reservation releases its portions.
- Store a nutrition-per-portion snapshot with the cooking batch. Later table edits or recipe substitutions should not change the recorded estimate for already prepared leftovers.

## Defaults and edge cases

- Automatically fill only the next day's same meal type, when the whole household can be covered. Keep later dates as explicit scheduling choices rather than silently filling an entire week.
- If only 1 extra portion remains for a 2-person household, show **1 portion available** and leave tomorrow's full meal unfilled. A later enhancement could combine leftover and freshly cooked portions, charging shopping only for the fresh portion.
- If tomorrow is occupied, retain unassigned portions and offer a choice of another empty slot. Do not replace the meal or quietly skip several days into the future.
- If the original batch changes before cooking, reconcile its dependent reservations and show which ones no longer have enough portions. If cooking produces fewer portions than planned, report the shortfall instead of displaying a covered meal.
- Use the prepared batch's use-by date and fridge/freezer state when suggesting meals. Do not invent one universal shelf life. A frozen batch can offer an explicit thaw/reheat plan.
- Removing an uncooked source plan should remove its expected follow-on reservations. Once portions have been eaten, undo the later consumption records before undoing the source cooking record, preserving ingredient and portion accounting.
- Make cooking and leftover-consumption actions safe to retry, with receipts and undo like the existing pantry flow.

## Useful later additions

Offer “use these first” suggestions, a prepared-food shelf beside the raw pantry, lunch-sized portions, a freeze/unfreeze action, and a weekly view of cooking effort saved. Each should use the same batch accounting rather than generating extra copies of the recipe's ingredient list.
