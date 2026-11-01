# Airport Recovery Live — Hackathon Demo

Present one recovery story, following it from the operator’s problem to the passenger’s response:

**A flight is disrupted → find alternatives → handle another change → give passengers a clear next step.**

Aim for **7–8 minutes**, allowing for provider response times. These steps match the current live-assisted app.

## Before presenting

- Prepare a flight number and date you have rehearsed.
- Prepare a small set of clearly labeled demo passenger groups.
- Have the passenger page ready in a second tab, or test the QR link on a device that can reach the application.
- Test your provider connections and rehearse the disruption in step 6.
- Keep a short recording of a successful rehearsal available if providers fail. Label it as a recording.

## 1. Introduce the problem — show the operations workspace

**Do:** Start on **Airport**, then briefly switch to **Network**. Point out the passenger recovery counters and the locations involved.

**Say:**

> When a flight is disrupted, the airline needs to coordinate alternatives for people with different deadlines, budgets and travel requirements.

**The audience should understand:** This is an operational coordination tool, with a shared picture of the recovery.

## 2. Start with a specific flight and incident

**Do:** Click **Start recovery**, enter your rehearsed flight number and **First tracked date (UTC)**, then click **Find flight**. Select the exact flight, describe the demo disruption and click **Save recovery case**. For a shorter presentation, open a prepared case and explain these fields.

**Say:**

> We identify the affected flight, then record the incident reported by the operator.

**The audience should see:** The case is anchored to an identifiable flight. Flight observations and the operator’s incident report have separate sources.

## 3. Make the problem about actual passenger requirements

**Do:** Show the case’s **Affected passenger groups**. If needed, use **Manage passenger requirements & services**, then return and **Link selected groups**. Have the demo groups prepared beforehand.

Select one group and highlight its destination, arrival deadline, total budget and requirement to stay together. Show accessibility requirements if your example includes them.

**Say:**

> A useful alternative must work for this whole group—not just provide a seat somewhere.

**The audience should see:** Recovery accounts for individual constraints. Clearly identify demo groups as planning assumptions.

## 4. Run the assisted recovery search

**Do:** In **Find sourced alternatives**, select the affected groups, choose **Balanced** or **Earliest arrival**, select **Flights**, and show the **Maximum provider calls** limit. Click **Search provider alternatives**.

While it runs, expand **Provider and decision record**.

**Say:**

> AI helps choose searches and select a plan. The backend checks whether the proposed journey satisfies the operational constraints.

**The audience should see:** Real sourcing activity, traceable decisions and a bounded search. Use the waiting time to explain the workflow.

## 5. Inspect one complete proposed journey

**Do:** Click **Review itinerary**. Walk through the departure, arrival, connections and total cost. Compare these with the group’s requirements, then briefly show the route on **Network**.

**Say:**

> Here is the proposed journey, its cost and the evidence behind it.

**The audience should see:** An actionable itinerary with sources and timings. If something remains unresolved, show the explicit gap rather than describing the journey as complete.

## 6. Introduce one additional disruption — demonstrate replanning

**Do:** Use a service from your rehearsed proposal. In the command box, submit this request, replacing the placeholders with the actual identifiers:

> For case [case ID], service [service ID] is unavailable. Recalculate for group [group ID], maximum eight calls.

Show the recorded report, the affected proposal and the new recovery attempt. Compare the result with the previous itinerary.

**Say:**

> Conditions can change again. The operator can report that change in ordinary language and request recovery for the affected group.

**The audience should see:** The application responds to changing conditions and maintains the scope of the request. A clear “no feasible alternative” result is also meaningful; do not promise that every disruption has a solution.

## 7. Switch to the passenger’s perspective

**Do:** Immediately after a fresh proposal is available, select its group and use **Open passenger offer**, or scan the QR code on a reachable device. Show the itinerary and click **Accept this proposal**. Return to the operator screen to show the updated status.

**Say:**

> The passenger receives a clear proposal and can accept or decline it. Their response becomes visible to the operations team.

**The audience should see:** The connection between planning and passenger communication. Explain that acceptance records a preference; supplier bookings still require separate confirmation.

## 8. Close on the operational outcome

**Do:** Return to the overview. Point out **Need a plan**, **Proposed / accepted**, and **Supplier confirmed**, using the actual numbers shown.

**Say:**

> We’ve moved from a disrupted flight to sourced alternatives, handled a changing condition, and collected the passenger’s response—all in the same operational workspace.

**The audience should understand:** The value is coordinated recovery, with clear visibility into what is proposed, unresolved and confirmed.

## Presentation notes

- Use typed commands for the main presentation. Keep voice as an optional extension after a successful rehearsal; the verification notes still leave complete spoken recovery open.
- Open the passenger view promptly after the latest result because quotes can expire quickly.
- Keep demo passenger assumptions, live supplier observations and any simulated operations clearly labeled.
- If a provider fails, show the visible error or use your labeled rehearsal recording. Do not present recorded results as a fresh live search.
- Describe a passenger’s acceptance as a preference, not a completed booking. The application does not purchase tickets, reserve rooms or dispatch vehicles.
