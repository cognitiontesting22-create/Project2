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

History displays the batch's made/eaten/remaining amounts and explicit date. A cooked source's follow-on card changes from **Expected leftovers** to **Leftovers ready**; undo changes it back.

## Implemented third stage

Both the uncooked source plan and its prepared batch store `reserved_portions` explicitly. Remaining is physical stock (or planned servings minus portions eaten before cooking); reserved is promised to uneaten leftover planner meals; unassigned is remaining minus reserved. Reservations never exceed remaining, and all three counts stay nonnegative. The fifth stage below reconciles reservations when cooking produces fewer portions than planned.

**I ate this** records a `LeftoverMeal` with its batch, planner meal, portions, local date, unique action token and undo time. It atomically reduces remaining and reserved under `lock_kitchen`, without touching pantry stock or the cooking receipt. The card shows **Eaten**. Retrying the same token or recording an already eaten meal changes nothing. Undone records keep their tokens even after their former batch or planner meal is removed.

History shows each active consumption separately with **Undo**. Undo returns its portions to both remaining and reserved for the same planner meal. Cooking undo is refused until all consumptions from its batch have been undone. It then removes the batch and restores stock as before, leaving the expected reservations on the source plan. An eaten planner meal cannot be removed or replaced; an uneaten one releases its reservation when removed or replaced.

For yield 4 with 2 eaten and Tuesday reserving 2, the source shows **2 remaining · 2 reserved · 0 unassigned**, then all zero after eating Tuesday's meal. Yield 6 starts at **4 remaining · 2 reserved · 2 unassigned** and becomes **2 remaining · 0 reserved · 2 unassigned**. Removing Tuesday's uneaten meal changes only reserved and unassigned.

## Implemented fourth stage

Shopping requirements include only uncooked cooking batches (`leftover_of` is empty). Leftover meals add no ingredients, planned-meal count, or recipe titles. Page, export, checkbox validation, and checked-item restocking all use that same calculation. A 2-serving recipe with 200 g rice, planned at 4 servings plus next-day leftovers, requires **400 g rice once**. A week containing only the follow-on leftover meal has no requirement from it. Recording the cooking batch removes its requirements as before; conversions, merging, and pantry subtraction are unchanged.

Each new prepared batch snapshots exact per-portion calories, protein, carbs, and fat from `recipe_nutrition` at its actual cooking yield, together with availability and complete/partial status. Fractions are stored as exact strings, and unavailable nutrients as missing values. Food-table edits/deletions and recipe ingredient/base-yield edits never recalculate the snapshot. Undo deletes it with the batch; recording again takes a fresh snapshot. Existing batches receive missing estimates rather than reconstructed historical numbers.

Cooked leftovers show the snapshot's calories rounded through `estimate_text`, or a dash if missing, with **Partial estimate** for incomplete snapshots. Expected leftovers show live recipe estimates. Personal planned nutrition uses snapshots for cooked-source leftovers and live estimates for all other meals. For example, a cooked 4-serving batch from 400 g at 130 kcal per 100 g keeps **130 kcal per portion** on its leftover card and a 1-portion personal total after the food is edited to 200; the recipe page shows the live **200 kcal per serving**. Personal portions retain the existing cap and validation message.

## Implemented fifth stage

Source cards show spare portions that cannot feed the whole household, such as **1 portion available** for 3 planned with 2 eaten. No leftover meal is created. An occupied next-day slot is preserved and reported by its actual weekday and meal, with the spare portions unassigned. No later day is silently filled, and leftovers are never combined with fresh cooking.

Every source card with at least a household's worth of unassigned portions offers **Schedule leftovers**. The POST-only `leftover_schedule` route creates a household-sized reservation on any explicitly chosen later date and meal type. It refuses occupied slots, dates on/before the source, insufficient unassigned portions, and dates after a cooked batch's use-by date. These refusals leave kitchen data unchanged. Cooking directly from a recipe without a planner source remains outside scheduling.

Each leftover meal now stores `allocated_portions` separately from its requested `portions_eaten`; the difference is its shortfall. Same-recipe source edits preserve all leftover slots and reconcile allocations from the earliest date, with breakfast before lunch before dinner on a shared date. Uncovered meals display **Short by N portions** and are reported by slot. Growing the plan covers existing requests again. Recipe replacement removes the old expected meals and names their slots. Removing an uncooked source removes every dependent meal and names all slots in one message. The automatic next-day rule runs only when the source has no leftover meal yet.

Cooking cuts existing allocations to fit the actual remainder, taking from the latest slots first. Extra yield remains unassigned and never enlarges a reservation automatically. With 3 made and 2 eaten against a plan for 4, remaining and reserved both become 1 and Tuesday shows **Short by 1 portion · 1 of 2 portions available**. Eating consumes that allocated portion only; a zero allocation says **There are no portions left to eat.** Retry, consumption undo, and cooked/eaten-plan protections still apply to partial meals.

The prepared batch keeps the source's pre-cooking servings and portions eaten. Cooking undo restores those quantities and full expected reservations. If extra actual yield was explicitly reserved after cooking, the pending source quantity is kept large enough to cover all those reservations too. Existing batches have no historical planned quantities, so undo uses their current plan as the starting point. All allocation, aggregate reservation, and remaining-portion mutations share the kitchen lock and transaction; receipts remain the only authority for restoring raw stock.

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
