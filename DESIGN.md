# Design System

## Purpose and scene

A graduate checks new openings at a desk in daylight, compares evidence, and opens applications. A light, restrained interface supports scanning without decorative content.

## Theme and color

Light tinted neutrals, one dark blue accent for actions and active selection. Warm text for uncertainty, green for a sponsorship mention, red for explicit restrictions. State is always expressed in words as well as color. All colors use OKLCH and are defined in `static/style.css`.

## Typography

Native system sans serif. Body and rows 14px, secondary metadata 11–12px, headings 20–26px. Roles use stronger weight; companies and timestamps remain secondary. No web font requests.

## Layout

One main column with a compact header, Daily edition / Saved / Applications tabs, a publication date and 6:30 PM Eastern window, and remaining / handled counts. The homepage contains at most 100 frozen postings. Search, country, role, and sponsorship are the only visible edition filters. Older editions are selectable. Additional filters and the uncertain discovery queue live outside the daily edition. Source health, collection controls, Plus settings, and cutoff preferences live in Settings. On small screens semantic job rows become stacked entries with Apply and decision controls visible without horizontal scrolling.

## Interaction

Native controls, visible keyboard focus, descriptive labels, and live status text. No decorative motion, charts, imagery, modals, or notification subscriptions. Opening an application reveals Did you apply? with Yes, applied and No, skip. Only an explicit decision changes status and removes the job. Applied and Skip buttons also work directly. Application history and notes survive refreshes, and a status can be restored from history/audit views. No replacement jobs are added after a decision.
