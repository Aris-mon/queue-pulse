# V8 Pie Chart + Dark Mode Update

## Dashboard charts
- Added responsive donut/pie visualization for response coverage.
- Added responsive priority-distribution donut using mutually exclusive Critical/High/Medium/Normal buckets.
- TAT Breached and Critical operational counts remain visible as separate legend/KPI values because those states can overlap other categories.
- Replaced the old five-card status strip and four-card priority block with more compact chart-led layouts.

## Dark mode
- Fixed white card/background leakage in Email Status and Priority summary.
- Added readable dark-mode text for labels, counts and helper text.
- Added dark hover states so tiles never switch to a light/white hover surface.

## Backend
- Added `normal` severity count to the dashboard context for the mutually-exclusive priority pie.
