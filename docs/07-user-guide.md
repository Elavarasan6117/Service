# Operations User Guide

For the people who use this system every day. No technical background assumed.

---

## What this system answers

One question: **can we service this new customer?**

It answers by measuring the **actual driving distance** by road from the new customer to the nearest of our existing service locations. If that distance is **2.00 km or less**, the answer is SERVICE AVAILABLE. If it is more, the answer is SERVICE NOT AVAILABLE.

It does not use the straight-line distance. Two shops can be 800 m apart on a map and 3 km apart by road if there is a river, a railway line or a one-way system between them. Only the road distance decides.

---

## Signing in

Go to the system address in your browser and sign in with the username and password your administrator gave you.

The badge in the top-right shows your name and role. What you can do depends on that role:

| Role | What you can do |
|---|---|
| **VIEW ONLY** | Look at the map, customers and history |
| **SALES** | The above, plus create customers and run checks |
| **OPERATIONS** | The above, plus correct customer locations on the map and manage service locations |
| **SUPERVISOR** | The above, plus import service locations and change most settings |
| **ADMIN** | Everything, including the 2 km threshold and user accounts |

Next to your name is a **Live** indicator. When it is green, your dashboard updates by itself as colleagues create customers — you never need to press refresh. If it says *Reconnecting*, it is repairing itself; carry on working.

---

## Adding a new customer

**1. Fill in the form on the left.** Customer ID and name are required. Everything else helps, and the address helps most.

**2. Type the address.** As you type, suggestions appear. Pick the closest one — this fills the address accurately and finds the coordinates automatically. Adding the **area** and **pincode** noticeably improves accuracy in Chennai, where street names repeat across the city.

**3. Check the marker on the map.** A black marker appears at the address the system found. **Look at it.** Address matching in Chennai is often approximate.

**4. If the marker is in the wrong place, drag it.** Drag it to the correct spot, then press **Confirm location & recalculate**. The system re-measures from the corrected position. This is a normal part of the job, not an error.

**5. Press "Create customer & check serviceability".** The result appears within a few seconds.

---

## Reading the result

### 🟢 SERVICE AVAILABLE

```
Nearest existing service   SRV-104 · Sri Foods
Driving distance           1.42 KM   (1,420 m by road)
Maximum allowed            2.00 KM
Inside limit by            580 m
```

The new customer is within road-distance reach of an existing service location. The green line on the map is the actual driving route the distance was measured along.

### 🔴 SERVICE NOT AVAILABLE

```
Nearest existing service   SRV-104 · Sri Foods
Driving distance           2.85 KM
Maximum allowed            2.00 KM
Outside limit by           850 m
```

The nearest existing service location is too far by road. The red line shows the driving route, so you can see *why* — often a detour that is not obvious from the map.

### ⚑ LOCATION VERIFICATION REQUIRED

The address could not be confirmed. **This does not mean the customer is unserviceable.** Nothing has been decided.

**What to do:** find the location on the map yourself, drag the marker onto it, and press *Confirm location & recalculate*.

### ⚠ ROUTE CALCULATION ERROR

The mapping service did not respond. **This does not mean the customer is unserviceable.** Nothing has been decided.

**What to do:** press *Retry calculation*. If it keeps failing, tell your supervisor — the mapping service may be down. Do not tell the customer they are out of coverage.

### ⚠ NO SERVICE LOCATIONS CONFIGURED

There are no active service locations to compare against. This is a setup problem, not an answer about this customer. Tell your administrator.

### ◑ PENDING REVIEW

Automatic decisions have been paused by an administrator, usually during an incident. Qualify this customer manually and note it for later.

---

## Reading the map

| Marker | Meaning |
|---|---|
| Purple square **W** | Warehouse |
| Small blue circle | Existing service location |
| Black circle | The new customer you are working on |
| Green circle | The nearest service location, when serviceable |
| Red circle | The nearest service location, when not serviceable |
| Amber circle | A customer needing location verification |
| Coloured line | The actual driving route the distance was measured along |

A **dashed** line means the route drawing was unavailable, so a straight line is shown for reference only. The distance in the panel is still the measured road distance — only the picture is approximate, never the number.

Click any marker for its details.

---

## The numbers at the top

| Tile | Meaning |
|---|---|
| New customers | Created today |
| Service available | Today's customers within the limit |
| Not available | Today's customers outside the limit |
| Verification req. | Waiting for someone to place a marker |
| Route errors | Checks that could not be measured — retry these |
| Avg. road distance | Average distance to the nearest service location today |

"Today" runs midnight to midnight, Chennai time.

---

## Recent serviceability checks

Every decision the system has ever made is listed, newest first, and is never deleted. Each row shows the customer, which service location was nearest, the road distance, the limit that applied **at that moment**, and who ran the check.

That last detail matters: if the 2 km limit is ever changed, every past decision still shows the limit it was actually judged against.

---

## Frequently asked

**The address search found nothing.**
Type a nearby landmark instead, then drag the marker to the exact spot. Or place the marker directly if you know the location.

**The distance looks too big for how close they are.**
That is usually correct and is the whole point of the system. Click *View route* and follow the line — a river, a flyover or a one-way system often makes a short hop a long drive.

**I moved the marker but the answer did not change.**
Press **Confirm location & recalculate**. Moving the marker alone does not re-run the check.

**Can I override a NOT AVAILABLE?**
Not directly. A supervisor can put a customer into review. The system records what the road distance actually is; the business decides what to do with borderline cases.

**Why does the same customer sometimes get a slightly different distance?**
Measurements are reused for 24 hours to control cost. After that, the mapping service is asked again, and road networks and its data change slightly over time.

**Who can change the 2 km limit?**
Only an administrator, and only with a written reason that is permanently recorded. Ask your supervisor if the business needs it changed.

---

## When something looks wrong

Report it rather than working around it. Include:

- the customer ID,
- what the system said,
- what you believe the correct answer is and why,
- the **Audit** reference shown in small text at the bottom of the result panel.

That reference lets an engineer retrieve the exact calculation — the coordinates used, the provider that answered, the distance returned and the limit applied.
