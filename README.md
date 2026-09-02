# GDG event digest

Twice a week (Monday and Thursday, 7 AM Toronto time) this pulls the next 14 days of
Google Developer Group events, keeps the ones you can actually get to, and gives you:

* a live web page you can bookmark (GitHub Pages),
* an email with the same content,
* speaker and host names for every event with a LinkedIn link (a direct profile
  when they listed one, otherwise a LinkedIn people search for their name and company),
* a NEW flag on anything that was not in the previous digest.

What gets kept:

* In person or hybrid events run by any Ontario chapter between Windsor and Ottawa
  (Thunder Bay, Sudbury, Sault Ste. Marie, Timmins, North Bay, Kenora are skipped)
  and any Michigan chapter.
* Online (virtual or hybrid) events from anywhere that start between 8 AM and 9 PM
  Toronto time.

Every run asks the site fresh, so events added or changed after the last digest
show up in the next one.

## One-time setup (about 10 minutes)

1. Create a new GitHub repository (public is fine, and public repos get free
   GitHub Pages). Upload every file in this folder, keeping the folder layout
   (`.github/workflows/digest.yml` must stay in that path).
2. In the repo go to Settings, then Pages. Under "Build and deployment" set
   Source to "GitHub Actions".
3. Optional, for the email: Settings, then Secrets and variables, then Actions.
   Add three repository secrets:
   * `MAIL_USERNAME`: the Gmail address that will send the digest
   * `MAIL_PASSWORD`: a Gmail App Password for that address (Google Account,
     Security, 2-Step Verification, App passwords). Not your normal password.
   * `MAIL_TO`: where the digest should land (can be the same address)
   Skip this step and the page still updates; only the email is skipped.
4. Go to the Actions tab, pick "GDG event digest", press "Run workflow" once.
   About a minute later the page is live at
   `https://<your-github-username>.github.io/<repo-name>/` and the first email
   is sent (if you set the secrets).

After that it runs on its own every Monday and Thursday. Press "Run workflow"
whenever you want an extra refresh.

## Tweaking

Everything adjustable is at the top of `gdg_digest.py`:

* `DAYS_AHEAD` (default 14)
* `IN_PERSON_STATES` and `EXCLUDE_CITY_RE` for the travel radius
* `ONLINE_EARLIEST_HOUR` and `ONLINE_LATEST_HOUR` for which online events count

The schedule lives in `.github/workflows/digest.yml` (`cron: "0 11 * * 1,4"` is
11:00 UTC on Monday and Thursday).

## Running it on your own computer instead

`python3 gdg_digest.py` (Python 3.9 or newer, no extra packages) writes
`docs/index.html`, `docs/digest.md`, and `docs/digest.json`. Open the HTML file
in a browser.
