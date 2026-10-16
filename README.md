# WWOZ Studio

A desktop app for listening to [WWOZ](https://www.wwoz.org), New Orleans' community radio
station, on your own schedule. It downloads shows from WWOZ's two-week archive (and your
favorite shows every week on its own), lists the songs in each one from the DJs' logs and
Shazam, records the live stream, and cuts recordings of festival broadcasts such as Jazz
Fest into one file per set. For Windows.

This is a personal project, not made by or connected with WWOZ. The recordings it makes
are for your own listening; please support the station at wwoz.org.

## Installing it

1. On this page, click the green **Code** button, then **Download ZIP**, and unzip it
   somewhere that will stay put, such as your Documents folder.
2. In the unzipped folder, double-click **Setup.cmd**. It installs what the app needs
   (Python 3.12, VLC media player, ffmpeg and a few Python packages; it asks Windows to
   install any that are missing), puts a **WWOZ Studio** shortcut on your desktop, and
   offers to download your favorite shows every Sunday at 9 AM. It also asks whether to
   add the Jazz Fest set finder, which is a 2 GB download; you can skip it.
3. Double-click **WWOZ Studio** on the desktop.

Your things stay outside the program's folder, so you can replace the program with a
newer download at any time: recordings go in **Music\WWOZ recordings** (pick another
folder with "Recordings folder..." on the WWOZ Archive page), and your favorite shows and
other settings in **Documents\WWOZ Studio**.

## Across the top

The dark band at the top shows what WWOZ is on the air with right now; **Listen live**
plays the live stream (click again to stop). The page names under it switch pages, and so
do Ctrl+1 to Ctrl+5. In the Player, Space plays or pauses, the left and right arrows skip
30 seconds (5 minutes with Shift), and clicking anywhere on the progress bar jumps there.

## The library

The Player's library has four sections.

**Jazz Fest 2026** holds only the festival's sets, day by day (Free Agents Brass Band,
Astral Project, and so on), each its own file cut from your recordings of WWOZ's
broadcast. A set marked "(recorded Apr 24)" is one WWOZ replayed from an earlier day.

**Blues & BBQ Fest 2026** (and any other festival WWOZ carries live in place of its
regular shows) works the same way: the 18 sets, day by day, cut from WWOZ's archived
broadcasts. A set that ran across two of the archive's 2-hour files is joined into one.
The cuts fall at the scheduled set changes, moved past any record the DJs logged during
a changeover, so a set's file can open with the end of the previous act, a record, or
the DJ's introduction.

**WWOZ shows** groups every show by name (New Orleans Music Show, Spirits of Congo
Square, The Morning Set...), each episode listed by date and host with the newest first.
It gathers the archive downloads, the regular shows cut out of your festival-day and
other recordings, and the shows that aired around a festival broadcast. Open any
episode and its songs are listed below the player; click one to jump to it.

**Full recordings** keeps the long recordings and broadcasts the pieces were cut from.

## What happens on its own

There are no tool buttons any more. With the app open, it works through these one at a
time, and the line under the song list says what it is doing:

- A recording without a song list gets one from the DJs' log (Spinitron), and a
  recording of regular programming is marked where each show begins, from WWOZ's
  calendar.
- A new recording of a Jazz Fest day is listened through for the DJs' announcements, and
  each performance is named from their words (20 to 40 minutes per day, at low priority).
- Every recording is cut into its sets and whole shows. After you fix a name by hand, it
  is cut again two minutes later, so a few quick fixes make one new cut.
- Festival broadcasts are cut into sets when they are downloaded.
- Every download gets a Shazam scan for songs the DJ didn't log, only while nothing is
  playing (about 10 minutes a show). Shazam stops answering for a while if asked too
  often; the scans then wait half an hour.

To fix a name, right-click the row in the song list: "Mark a set or song starting now"
adds one at the playing position (type the artist for a set, or artist - song for a
song), and you can rename or delete a row. Right-clicking a recording in the library
offers "Get the DJs' song list again", "Find the sets again" and "Show the file in its
folder".

How a Jazz Fest set was named shows under its title in the Player: the DJs named it on
the air (and it matches the schedule), they named it once, the name comes from the
schedule because nobody said it (worth a listen), or nobody named it at all. The DJ
transcripts behind each name are kept in the recording's `.autocut.json`.

A new festival WWOZ broadcasts needs its set times in a `fest-*.json` beside the code
(`fest-bluesbbq-2026.json` holds the 2026 Crescent City Blues & BBQ Festival): copy it,
then change the name, the words that appear in the archive's titles of the broadcasts,
and the sets.

## The other pages

**Shows** is the whole collection as individual, sendable files: every set and every
episode, searchable and sortable by clicking a heading, with the songs each one holds.
Double-click to play, "Open file location" to grab the mp3 and send it, "Rename..." to
fix a label.

**Jazz Fest Guide** is the full 2026 festival schedule (every stage, every set, all 8
days). Double-click a set to jump the player to its scheduled time.

**WWOZ Archive** is everything in WWOZ's 2-week on-demand archive. Select shows and
click Download (or double-click one); a check mark means you have it. "Record live
stream..." captures the live broadcast to an mp3, which the app then handles like any
other recording.

**Weekly Favorites** is WWOZ's whole weekly schedule. Check the shows you love, and every
Sunday at 9 AM Windows downloads their new episodes (task "WWOZ Weekly Archive
Download", log `weekly.log`). Click a show's name to read about it; "Download an
episode..." grabs one right now. "Refresh schedule from wwoz.org" re-reads the site
(a couple of minutes); otherwise the schedule is cached for a week in `wwoz_schedule.json`.

## Under the hood (optional)

Everything also works from a terminal in this folder:
`py -3.12 wwoz.py list | schedule | download | tracklist | identify | autocut | split | festival | record | weekly`
(`festival` marks and cuts every festival in a `fest-*.json` whose broadcasts are downloaded.)
Errors the app runs into are written to `wwoz_studio_errors.log` in Documents\WWOZ Studio.
(`schedule -v` prints the weekly grid with a description under each show.)
Files produced per recording: `.tracklist.csv` (spreadsheet), `.cue` (VLC/foobar2000
can use it to show chapters), `.shazam.csv` (fingerprint results).
`py -3.12 batch_autocut.py --split` relabels and re-splits every festival day in
one go. The festival schedule lives in `jazzfest2026_sets.json`; for another year,
download that year's "8-day cubes" PDF from nojazzfest.com/downloads and run
`py -3.12 parse_jazzfest_schedule.py <pdf>` (re-download it after the festival: it
is revised when days are cut short).
`wwoz.py --log FILE <command>` appends everything to FILE, for running windowless
with `pythonw.exe` (e.g. the Sunday task, which Setup points at Documents\WWOZ
Studio\weekly.log). The environment variables `WWOZ_RECORDINGS` and `WWOZ_DATA` move the
recordings and settings folders, and `setup.ps1 -DryRun` shows what Setup would do.
Remove the weekly automation: `Unregister-ScheduledTask -TaskName "WWOZ Weekly Archive Download"`.
