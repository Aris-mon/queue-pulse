# V10.20.2 Administration Network Layout Fix

- Removed the three duplicated Windows Server Network Configuration blocks from `templates/admin.html`.
- Repaired the Jinja block structure (`page_title`, `page_subtitle`, `content`) so Administration renders only once.
- Kept a single Windows Server Network Configuration card at the top of Administration.
- Reworked the network form into a two-column layout on desktop and a single-column responsive layout on smaller screens.
- Grouped network summary values into three aligned tiles.
- Kept Outlook, sync, policy, system events and AI cards below the network configuration in consistent two-column sections.
