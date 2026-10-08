-- Unity Catalog functions behind the agents: Bike, walk or wait (FR-15) and the Bunching spotter (FR-16), ADR-0007.
-- scripts/setup_agents.py replaces __S__ with `catalog`.`schema` and runs each statement
-- (separated by lines of dashes). decide_trip is created by the script from src/agents/rules.py.
-- Outside calls go through UC HTTP connections hsl_fmi and hsl_digitransit; the Digitransit key
-- comes from secret scope hsl_live_transit (NFR-6).

CREATE OR REPLACE FUNCTION __S__.find_stop(name STRING COMMENT 'Stop name or its start, e.g. "Kaivopuisto"')
RETURNS TABLE (stop_id STRING, stop_name STRING, stop_code STRING, lat DOUBLE, lon DOUBLE, is_tram_stop BOOLEAN)
COMMENT 'Finds HSL Stops by name from the daily GTFS load. Exact matches first, then tram Stops. Each direction usually has its own Stop with the same name.'
RETURN
  SELECT stop_id, stop_name, stop_code, lat, long, hsl_vehicle_type = 0
  FROM __S__.silver_stops
  WHERE lower(stop_name) = lower(trim(find_stop.name)) OR stop_name ILIKE concat(trim(find_stop.name), '%')
  ORDER BY lower(stop_name) = lower(trim(find_stop.name)) DESC, hsl_vehicle_type = 0 DESC, stop_name, stop_id
  LIMIT 10;

------------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION __S__.weather_outlook(lat DOUBLE, lon DOUBLE)
RETURNS TABLE (
  forecast_at TIMESTAMP, precipitation_mm_h DOUBLE, rain_probability_pct DOUBLE,
  wind_ms DOUBLE, gust_ms DOUBLE, temperature_c DOUBLE
)
COMMENT 'Hourly weather forecast at a point for the current and next two hours, from the Finnish Meteorological Institute (FMI) open data, CC BY 4.0. gust_ms is the hourly maximum gust.'
RETURN
  WITH r AS (
    SELECT http_request(
      conn => 'hsl_fmi', method => 'GET', path => 'wfs',
      params => map(
        'service', 'WFS', 'version', '2.0.0', 'request', 'getFeature',
        'storedquery_id', 'fmi::forecast::edited::weather::scandinavia::point::simple',
        'latlon', format_string('%.5f,%.5f', weather_outlook.lat, weather_outlook.lon),
        'parameters', 'Precipitation1h,PoP,WindSpeedMS,HourlyMaximumGust,Temperature',
        'timestep', '60',
        'starttime', date_format(date_trunc('HOUR', current_timestamp()), "yyyy-MM-dd'T'HH:mm:ss'Z'"),
        'endtime', date_format(date_trunc('HOUR', current_timestamp()) + INTERVAL 2 HOURS, "yyyy-MM-dd'T'HH:mm:ss'Z'")
      )
    ).text AS x
  ),
  e AS (
    SELECT
      xpath(x, '//*[local-name()="Time"]/text()') AS t,
      xpath(x, '//*[local-name()="ParameterName"]/text()') AS n,
      xpath(x, '//*[local-name()="ParameterValue"]/text()') AS v
    FROM r
  ),
  z AS (
    SELECT to_timestamp(p.t) AS at, p.n AS name, nullif(try_cast(p.v AS DOUBLE), double('NaN')) AS value
    FROM e, LATERAL explode(arrays_zip(e.t, e.n, e.v)) AS x(p)
  )
  SELECT
    at,
    max(CASE WHEN name = 'Precipitation1h' THEN value END),
    max(CASE WHEN name = 'PoP' THEN value END),
    max(CASE WHEN name = 'WindSpeedMS' THEN value END),
    max(CASE WHEN name = 'HourlyMaximumGust' THEN value END),
    max(CASE WHEN name = 'Temperature' THEN value END)
  FROM z
  GROUP BY at
  ORDER BY at;

------------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION __S__.tram_lateness(route_name STRING COMMENT 'Route as riders know it, e.g. "4" or "15"')
RETURNS TABLE (
  vehicle_id STRING, mode STRING, route STRING, direction STRING, journey_id STRING,
  lateness_s INT, last_seen_at TIMESTAMP, age_s BIGINT, freshness STRING
)
COMMENT 'Current Lateness of each Vehicle on a Route, from our own HSL feed (gold_vehicle_current). lateness_s is seconds behind schedule, positive = late; null = not published (always for metro). freshness: fresh (<= 30 s), fading (30 s - 5 min), hidden (> 5 min, do not treat as live).'
RETURN
  SELECT
    vehicle_id, mode, route, direction, journey_id, lateness_s, last_seen_at,
    timestampdiff(SECOND, last_seen_at, current_timestamp()),
    CASE
      WHEN timestampdiff(SECOND, last_seen_at, current_timestamp()) <= 30 THEN 'fresh'
      WHEN timestampdiff(SECOND, last_seen_at, current_timestamp()) <= 300 THEN 'fading'
      ELSE 'hidden'
    END
  FROM __S__.gold_vehicle_current
  WHERE route = trim(tram_lateness.route_name)
  ORDER BY direction, last_seen_at DESC;

------------------------------------------------------------------------------------------------

-- Pure parser for the Digitransit response, kept apart from the HTTP call so it can be tested
-- with a literal body (scripts/setup_agents.py --check).
CREATE OR REPLACE FUNCTION __S__.parse_trip_options(body STRING)
RETURNS TABLE (
  option STRING, leave_at TIMESTAMP, arrive_at TIMESTAMP, arrive_in_s INT,
  route STRING, direction STRING, operating_day STRING, journey_start STRING, board_stop STRING,
  scheduled_departure TIMESTAMP, expected_departure TIMESTAMP, realtime BOOLEAN,
  bike_station STRING, bikes_available INT, dock_station STRING, docks_free INT, error STRING
)
COMMENT 'Parses a Digitransit planConnection response (aliases tram, walk, bike) into one row per option. Internal helper of trip_options.'
RETURN
  WITH j AS (
    SELECT from_json(body, '
      data STRUCT<
        tram: STRUCT<edges: ARRAY<STRUCT<node: STRUCT<start: STRING, `end`: STRING, legs: ARRAY<STRUCT<
          mode: STRING, serviceDate: STRING,
          start: STRUCT<scheduledTime: STRING, estimated: STRUCT<time: STRING>>,
          route: STRUCT<shortName: STRING>,
          trip: STRUCT<directionId: STRING, departureStoptime: STRUCT<scheduledDeparture: INT>>,
          `from`: STRUCT<name: STRING>>>>>>>,
        walk: STRUCT<edges: ARRAY<STRUCT<node: STRUCT<start: STRING, `end`: STRING>>>>,
        bike: STRUCT<edges: ARRAY<STRUCT<node: STRUCT<start: STRING, `end`: STRING, legs: ARRAY<STRUCT<
          mode: STRING,
          `from`: STRUCT<vehicleRentalStation: STRUCT<name: STRING, availableVehicles: STRUCT<total: INT>>>,
          `to`: STRUCT<vehicleRentalStation: STRUCT<name: STRING, availableSpaces: STRUCT<total: INT>>>>>>>>>>,
      errors ARRAY<STRUCT<message: STRING>>') AS r
  ),
  -- First itinerary that actually rides a tram (the planner may also offer a walk-only one).
  tram AS (
    SELECT n, try_element_at(filter(n.legs, l -> l.mode = 'TRAM'), 1) AS t
    FROM j, LATERAL posexplode(j.r.data.tram.edges) AS p(pos, e), LATERAL (SELECT e.node AS n)
    WHERE exists(e.node.legs, l -> l.mode = 'TRAM')
    ORDER BY pos
    LIMIT 1
  ),
  -- Bike rental must be planned together with WALK, so skip walk-only itineraries.
  bike AS (
    SELECT e.node AS n, try_element_at(filter(e.node.legs, l -> l.mode = 'BICYCLE'), 1) AS b
    FROM j, LATERAL posexplode(j.r.data.bike.edges) AS p(pos, e)
    WHERE exists(e.node.legs, l -> l.mode = 'BICYCLE')
    ORDER BY pos
    LIMIT 1
  ),
  walk AS (
    SELECT try_element_at(j.r.data.walk.edges, 1).node AS n FROM j
  )
  SELECT
    'tram', to_timestamp(n.start), to_timestamp(n.`end`),
    CAST(timestampdiff(SECOND, current_timestamp(), to_timestamp(n.`end`)) AS INT),
    t.route.shortName, CAST(CAST(t.trip.directionId AS INT) + 1 AS STRING), t.serviceDate,
    format_string('%02d:%02d', t.trip.departureStoptime.scheduledDeparture DIV 3600,
                  (t.trip.departureStoptime.scheduledDeparture % 3600) DIV 60),
    t.`from`.name, to_timestamp(t.start.scheduledTime),
    coalesce(to_timestamp(t.start.estimated.time), to_timestamp(t.start.scheduledTime)),
    t.start.estimated.time IS NOT NULL,
    NULL, NULL, NULL, NULL, NULL
  FROM tram
  UNION ALL
  SELECT
    'walk', to_timestamp(n.start), to_timestamp(n.`end`),
    CAST(timestampdiff(SECOND, current_timestamp(), to_timestamp(n.`end`)) AS INT),
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM walk WHERE n IS NOT NULL
  UNION ALL
  SELECT
    'bike', to_timestamp(n.start), to_timestamp(n.`end`),
    CAST(timestampdiff(SECOND, current_timestamp(), to_timestamp(n.`end`)) AS INT),
    NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
    b.`from`.vehicleRentalStation.name, b.`from`.vehicleRentalStation.availableVehicles.total,
    b.`to`.vehicleRentalStation.name, b.`to`.vehicleRentalStation.availableSpaces.total, NULL
  FROM bike WHERE b IS NOT NULL
  UNION ALL
  SELECT 'error', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
         coalesce(array_join(transform(j.r.errors, x -> x.message), '; '), 'unreadable response')
  FROM j WHERE j.r.errors IS NOT NULL OR j.r.data IS NULL;

------------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION __S__.trip_options(from_lat DOUBLE, from_lon DOUBLE, to_lat DOUBLE, to_lon DOUBLE)
RETURNS TABLE (
  option STRING, leave_at TIMESTAMP, arrive_at TIMESTAMP, arrive_in_s INT,
  route STRING, direction STRING, operating_day STRING, journey_start STRING, board_stop STRING,
  scheduled_departure TIMESTAMP, expected_departure TIMESTAMP, realtime BOOLEAN,
  bike_station STRING, bikes_available INT, dock_station STRING, docks_free INT, error STRING
)
COMMENT 'Leaving now between two points: the earliest tram trip, walking, and city bike (HSL Digitransit journey planner, with HSL real-time departures). One row per option; arrive_in_s is seconds from now to arrival. An error row explains a failed call.'
RETURN
  WITH h AS (
    SELECT http_request(
      conn => 'hsl_digitransit', method => 'POST', path => 'gtfs/v1',
      params => map('digitransit-subscription-key', secret('hsl_live_transit', 'digitransit_key')),
      json => to_json(named_struct(
        'query', 'query($o: PlanLabeledLocationInput!, $d: PlanLabeledLocationInput!) {
            tram: planConnection(origin: $o, destination: $d, first: 3, modes: {transit: {transit: [{mode: TRAM}]}}) {
              edges { node { start end legs { mode serviceDate
                start { scheduledTime estimated { time } } route { shortName }
                trip { directionId departureStoptime { scheduledDeparture } } from { name } } } } }
            walk: planConnection(origin: $o, destination: $d, first: 1, modes: {directOnly: true, direct: [WALK]}) {
              edges { node { start end } } }
            bike: planConnection(origin: $o, destination: $d, first: 3, modes: {directOnly: true, direct: [WALK, BICYCLE_RENTAL]}) {
              edges { node { start end legs { mode
                from { vehicleRentalStation { name availableVehicles { total } } }
                to { vehicleRentalStation { name availableSpaces { total } } } } } } }
          }',
        'variables', named_struct(
          'o', named_struct('location', named_struct('coordinate',
                 named_struct('latitude', trip_options.from_lat, 'longitude', trip_options.from_lon))),
          'd', named_struct('location', named_struct('coordinate',
                 named_struct('latitude', trip_options.to_lat, 'longitude', trip_options.to_lon)))
        )
      ))
    ) AS r
  )
  SELECT p.*
  FROM h, LATERAL __S__.parse_trip_options(
    CASE WHEN h.r.status_code = 200 THEN h.r.text
         ELSE to_json(named_struct('errors', array(named_struct('message',
                concat('Digitransit HTTP ', h.r.status_code, ': ', substr(h.r.text, 1, 200))))))
    END
  ) AS p;

------------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION __S__.bike_walk_or_wait(
  from_stop STRING COMMENT 'Tram Stop the rider is at, by name, e.g. "Kaivopuisto"',
  to_stop STRING COMMENT 'Stop the rider wants to get to, by name, e.g. "Rautatientori"'
)
RETURNS TABLE (
  choice STRING, reason STRING, from_stop STRING, to_stop STRING,
  tram_route STRING, tram_direction STRING, tram_departs_in_min INT, tram_arrives_in_min INT,
  tram_departure_realtime BOOLEAN, measured_lateness_s INT, measured_age_s BIGINT, feed_live BOOLEAN,
  walk_arrives_in_min INT, bike_arrives_in_min INT,
  bike_station STRING, bikes_available INT, dock_station STRING, docks_free INT,
  precipitation_mm_h DOUBLE, rain_probability_pct DOUBLE, gust_ms DOUBLE, temperature_c DOUBLE,
  in_bike_season BOOLEAN, problems STRING
)
COMMENT 'Bike, walk or wait (FR-15): should a rider at a tram Stop wait for the tram, walk, or take a city bike? Combines the next tram (HSL real-time), our measured Lateness for that tram, city bike availability and the FMI weather forecast for the next hour, then applies fixed rules (decide_trip). choice is wait, walk, bike or none; reason explains it. measured_lateness_s is null when our feed is not live or the tram was not seen in the last 5 min: then say Lateness is unknown, never on time. problems lists missing inputs.'
RETURN
  WITH
  o AS (SELECT * FROM __S__.find_stop(bike_walk_or_wait.from_stop) LIMIT 1),
  d AS (SELECT * FROM __S__.find_stop(bike_walk_or_wait.to_stop) LIMIT 1),
  opts AS (SELECT t.* FROM o, d, LATERAL __S__.trip_options(o.lat, o.lon, d.lat, d.lon) AS t),
  trip AS (
    SELECT
      max(CASE WHEN option = 'tram' THEN arrive_in_s END) AS tram_arrive_s,
      max(CASE WHEN option = 'walk' THEN arrive_in_s END) AS walk_arrive_s,
      max(CASE WHEN option = 'bike' THEN arrive_in_s END) AS bike_arrive_s,
      max(route) FILTER (WHERE option = 'tram') AS route,
      max(direction) FILTER (WHERE option = 'tram') AS direction,
      max(operating_day) FILTER (WHERE option = 'tram') AS operating_day,
      max(journey_start) FILTER (WHERE option = 'tram') AS journey_start,
      max(expected_departure) FILTER (WHERE option = 'tram') AS expected_departure,
      max(realtime) FILTER (WHERE option = 'tram') AS realtime,
      max(bike_station) AS bike_station, max(bikes_available) AS bikes_available,
      max(dock_station) AS dock_station, max(docks_free) AS docks_free,
      max(error) AS error
    FROM opts
  ),
  wx AS (
    SELECT max(precipitation_mm_h) AS p, max(rain_probability_pct) AS pop, max(gust_ms) AS g, min(temperature_c) AS temp
    FROM o, LATERAL __S__.weather_outlook(o.lat, o.lon) AS w
    WHERE w.forecast_at <= current_timestamp() + INTERVAL 1 HOUR
  ),
  feed AS (
    SELECT coalesce(max(heartbeat_at) >= current_timestamp() - INTERVAL 30 SECONDS, false) AS live
    FROM __S__.silver_heartbeats
    WHERE heartbeat_at >= current_timestamp() - INTERVAL 1 HOUR
  ),
  -- Our own Lateness for the very journey the planner offers (journey_id = route|dir|oday|start).
  ours AS (
    SELECT max_by(v.lateness_s, v.last_seen_at) AS lateness_s,
           min(timestampdiff(SECOND, v.last_seen_at, current_timestamp())) AS age_s
    FROM trip JOIN __S__.gold_vehicle_current v
      ON v.journey_id = concat_ws('|', trip.route, trip.direction, trip.operating_day, trip.journey_start)
  ),
  facts AS (
    SELECT
      o.stop_name AS from_name, d.stop_name AS to_name, trip.*, wx.*, feed.live,
      ours.lateness_s AS our_lateness_s, ours.age_s AS our_age_s,
      month(current_date()) BETWEEN 4 AND 10 AS in_season
    FROM trip CROSS JOIN wx CROSS JOIN feed CROSS JOIN ours
    LEFT JOIN o ON true LEFT JOIN d ON true
  ),
  decided AS (
    SELECT *, __S__.decide_trip(tram_arrive_s, walk_arrive_s, bike_arrive_s, p, pop, g, temp, in_season) AS decision
      , error IS NOT NULL AND coalesce(tram_arrive_s, walk_arrive_s, bike_arrive_s) IS NULL AS planner_down
    FROM facts
  )
  SELECT
    CASE WHEN from_name IS NULL OR to_name IS NULL OR planner_down THEN 'none' ELSE decision.choice END,
    CASE WHEN from_name IS NULL THEN concat('no Stop called "', bike_walk_or_wait.from_stop, '"')
         WHEN to_name IS NULL THEN concat('no Stop called "', bike_walk_or_wait.to_stop, '"')
         WHEN planner_down THEN 'the HSL journey planner did not answer, so there is nothing to compare'
         ELSE decision.reason END,
    from_name, to_name,
    route, direction,
    CAST(round(timestampdiff(SECOND, current_timestamp(), expected_departure) / 60) AS INT),
    CAST(round(tram_arrive_s / 60) AS INT),
    realtime,
    CASE WHEN live AND our_age_s <= 300 THEN our_lateness_s END,
    our_age_s, live,
    CAST(round(walk_arrive_s / 60) AS INT), CAST(round(bike_arrive_s / 60) AS INT),
    bike_station, bikes_available, dock_station, docks_free,
    p, pop, g, temp, in_season,
    nullif(concat_ws('; ',
      error,
      CASE WHEN p IS NULL AND g IS NULL THEN 'no weather forecast' END,
      CASE WHEN NOT live THEN 'our HSL feed is not running, so measured Lateness is unknown' END
    ), '')
  FROM decided;

------------------------------------------------------------------------------------------------

-- Bunching spotter (FR-16). Headway and Bunching come from departure Stop Events only (trams;
-- metro has none, ADR-0004), so no stop sequence or shapes are needed.
CREATE OR REPLACE FUNCTION __S__.tram_bunching(
  since TIMESTAMP COMMENT 'Start of the window (the second departure of a pair falls in it)',
  until TIMESTAMP COMMENT 'End of the window',
  max_share DOUBLE COMMENT 'A pair is Bunching when Headway < max_share x scheduled headway; default 0.25'
)
RETURNS TABLE (
  route STRING, direction STRING, stop_id STRING, stop_name STRING, stop_lat DOUBLE, stop_long DOUBLE,
  leader_vehicle_id STRING, follower_vehicle_id STRING, leader_departed_at TIMESTAMP, follower_departed_at TIMESTAMP,
  headway_s INT, scheduled_headway_s INT, leader_lateness_s INT, follower_lateness_s INT, bunching BOOLEAN
)
COMMENT 'Headway between consecutive trams of the same Route and direction departing the same Stop (FR-16.1). scheduled_headway_s is the planned frequency there: the median gap between consecutive scheduled departures in that hour. bunching = Headway below max_share of it (FR-16.2). One row per consecutive pair; the follower is the tram behind, the one to recommend.'
RETURN
  WITH d AS (
    SELECT route, direction, stop_id, stop_name, stop_lat, stop_long, vehicle_id, journey_id,
           departed_at, scheduled_departure, lateness_s
    FROM __S__.gold_departures
    -- One extra hour so the first departure in the window still has the one before it.
    WHERE departed_at >= tram_bunching.since - INTERVAL 1 HOUR AND departed_at < tram_bunching.until
  ),
  -- Planned frequency: median gap between consecutive scheduled departures per Stop and hour. Not the
  -- gap between the pair's own journeys: a tram hours late would make that gap meaningless. Gaps over
  -- an hour mean departures we didn't observe (Data Gaps, short demo runs), not the plan: ignored.
  s AS (
    SELECT *, percentile_approx(sched_gap, 0.5) OVER (
      PARTITION BY route, direction, stop_id, date_trunc('HOUR', scheduled_departure)) AS planned_gap
    FROM (
      SELECT *, CASE WHEN g > 0 AND g <= 3600 THEN g END AS sched_gap
      FROM (
        SELECT *, timestampdiff(SECOND, lag(scheduled_departure) OVER (
          PARTITION BY route, direction, stop_id ORDER BY scheduled_departure), scheduled_departure) AS g
        FROM d
      )
    )
  ),
  p AS (
    SELECT *,
      lag(vehicle_id) OVER w AS leader_vehicle_id,
      lag(journey_id) OVER w AS leader_journey_id,
      lag(departed_at) OVER w AS leader_departed_at,
      lag(lateness_s) OVER w AS leader_lateness_s
    FROM s
    WINDOW w AS (PARTITION BY route, direction, stop_id ORDER BY departed_at)
  ),
  h AS (
    SELECT *,
      CAST(timestampdiff(SECOND, leader_departed_at, departed_at) AS INT) AS headway,
      CAST(planned_gap AS INT) AS scheduled_headway
    FROM p
    WHERE leader_vehicle_id IS NOT NULL AND leader_vehicle_id <> vehicle_id AND leader_journey_id <> journey_id
      AND departed_at >= tram_bunching.since
  )
  SELECT
    route, direction, stop_id, stop_name, stop_lat, stop_long,
    leader_vehicle_id, vehicle_id, leader_departed_at, departed_at,
    headway, nullif(scheduled_headway, 0), leader_lateness_s, lateness_s,
    coalesce(scheduled_headway > 0 AND headway < coalesce(tram_bunching.max_share, 0.25) * scheduled_headway, false)
  FROM h;

------------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION __S__.bunching_now(route_name STRING COMMENT 'Route as riders know it, e.g. "4"; empty or null = all tram Routes')
RETURNS TABLE (
  feed_live BOOLEAN, route STRING, direction STRING, stop_name STRING, leader_vehicle_id STRING,
  follower_vehicle_id STRING, headway_s INT, scheduled_headway_s INT, leader_lateness_s INT,
  follower_lateness_s INT, follower_departed_at TIMESTAMP, age_s BIGINT
)
COMMENT 'Bunching happening now (FR-16.3, FR-16.4): pairs of trams on the same Route and direction that left their latest common Stop within the last 5 minutes with a Headway below 25 % of the planned frequency. The follower is the tram behind. Always at least one row: feed_live = false means our HSL feed is not running, so Bunching is unknown (not "none"); feed_live = true with an empty route means no Bunching now.'
RETURN
  WITH feed AS (
    SELECT coalesce(max(heartbeat_at) >= current_timestamp() - INTERVAL 30 SECONDS, false) AS live
    FROM __S__.silver_heartbeats
    WHERE heartbeat_at >= current_timestamp() - INTERVAL 1 HOUR
  ),
  pairs AS (
    SELECT route, direction, stop_name, leader_vehicle_id, follower_vehicle_id, headway_s, scheduled_headway_s,
           leader_lateness_s, follower_lateness_s, follower_departed_at,
           timestampdiff(SECOND, follower_departed_at, current_timestamp()) AS age_s
    FROM __S__.tram_bunching(current_timestamp() - INTERVAL 5 MINUTES, current_timestamp(), 0.25)
    WHERE bunching AND (nullif(trim(bunching_now.route_name), '') IS NULL OR route = trim(bunching_now.route_name))
    -- A bunched pair shows up at every Stop it passes; keep its latest one.
    QUALIFY row_number() OVER (
      PARTITION BY route, direction, leader_vehicle_id, follower_vehicle_id ORDER BY follower_departed_at DESC) = 1
  )
  SELECT feed.live, pairs.* FROM feed LEFT JOIN pairs ON true
  ORDER BY pairs.headway_s;
