# HSL Live Transit

A near-real-time view of Helsinki (HSL) trams and metro: where each vehicle is right now and how punctual the service is.

## Language

### Fleet and service

**Mode**:
The kind of transport a vehicle belongs to. In scope: tram and metro. HSL publishes Lateness and Stop Events for trams only.
_Avoid_: Transport type, vehicle type

**Vehicle**:
A physical tram or metro unit, identified by operator id + vehicle number.
_Avoid_: Bus, unit, car

**Route**:
A public line as riders know it (e.g. tram "4"), travelled in one of two directions.
_Avoid_: Line (ambiguous with HSL's internal line id)

**Journey**:
One scheduled run of a route in one direction, starting at a specific scheduled time on an operating day. A vehicle performs many journeys per day.
_Avoid_: Trip, run, service

**Operating Day**:
The service date a journey belongs to; can differ from the calendar date for journeys after midnight.
_Avoid_: Date, day

### Events

**Position Event**:
A roughly once-per-second report of a vehicle's location, speed, heading and lateness while on a journey (HSL "VP").
_Avoid_: Ping, GPS update, location

**Stop Event**:
A report that a vehicle arrived at, is about to depart, or departed a stop (HSL "ARR", "PDE", "DEP"). The authoritative source for punctuality at stops.
_Avoid_: Stop ping, arrival

**Lateness**:
How far behind schedule a journey is, in seconds; positive means late, negative means early. Equal to the negation of HSL's `dl`. *Unknown* when HSL publishes none (always for metro), and unknown is never treated as on-time.
_Avoid_: Delay, dl, offset

**Timetable Lateness**:
How far a Stop Event is from the public timetable's scheduled time (whole minutes), in seconds; positive means late. Shown next to Lateness for comparison, never in place of it.
_Avoid_: Schedule delay, raw lateness

**Stop**:
A physical boarding point served by routes, with a name and coordinates from HSL's published timetable.
_Avoid_: Station, platform, halt

### Punctuality

**On-time**:
A departure Stop Event whose lateness falls inside the on-time window chosen by the viewer (default: 60 s early to 180 s late). Arrivals are never judged.
_Avoid_: Punctual, on schedule

**Punctuality**:
The share of departure Stop Events in a period that were on-time. Exists for trams only.
_Avoid_: OTP, reliability

### Live map

**Freshness**:
How recently a vehicle last sent a Position Event: *fresh* (≤ 30 s), *fading* (30 s – 5 min), or *hidden* (> 5 min).
_Avoid_: Staleness, age, TTL

### Riders

**Rider Report**:
A problem a viewer reports about one Vehicle (late, early, crowded, skipped stop, breakdown, other), stored with what we measured for that Vehicle at that moment. A claim by a rider, never a measurement: it is never mixed into Lateness or Punctuality.
_Avoid_: Complaint, feedback, incident

**My Routes**:
The Routes a signed-in viewer chose to watch; kept per viewer across visits.
_Avoid_: Favourites, subscriptions

### Observation

**Ingestion Session**:
A continuous period during which we were subscribed to the HSL feed.
_Avoid_: Run, stream run

**Data Gap**:
A period outside any ingestion session. Events from a data gap are permanently missing and must never be read as "no traffic".
_Avoid_: Outage, downtime (those describe HSL, not us)

**Coverage**:
The fraction of a time window that falls inside ingestion sessions. Every aggregate shown to a viewer carries its coverage.
_Avoid_: Completeness, uptime
