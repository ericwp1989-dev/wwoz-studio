# WWOZ Studio

A desktop app for listening to [WWOZ](https://www.wwoz.org), New Orleans' community radio
station, on your own schedule. It downloads shows from WWOZ's two-week archive (your
favorite shows every week on its own, and the festivals WWOZ broadcasts live), lists the
songs in each one from the DJs' logs and Shazam, records the live stream, and cuts
recordings of festival broadcasts such as Jazz Fest into one file per set. For Windows
and for Macs.

This is a personal project, not made by or connected with WWOZ. The recordings it makes
are for your own listening; please support the station at wwoz.org.

## Installing it on Windows

1. On this page, click the green **Code** button, then **Download ZIP**. Right-click the
   downloaded file, choose **Extract All**, and extract it somewhere that will stay put,
   such as your Documents folder. (Opening the ZIP and double-clicking inside it isn't
   enough: Setup needs the extracted folder.)
2. In the extracted folder, double-click **Setup.cmd**. Windows will probably show a blue
   box saying "Windows protected your PC", because the file came from the internet and
   isn't from a big company: click **More info**, then **Run anyway**.

   Setup installs what the app needs (Python 3.12, ffmpeg and a few Python packages;
   Windows may ask for permission to install them, so say yes), puts a **WWOZ Studio**
   shortcut on your desktop, and offers to download your favorite shows every Sunday at
   9 AM. It also asks whether to add the Jazz Fest set finder, which is a 2 GB download;
   you can skip it. It takes a few minutes and ends with "Done".
3. Double-click **WWOZ Studio** on the desktop.

If you later move the folder, or replace it with a newer download, double-click
Setup.cmd again from the new folder, so the shortcut and the Sunday download find it.

## Installing it on a Mac

The Mac version passes every check on GitHub's test Macs (macOS 14 and 15): Setup,
downloading and playing a show, its song list, Shazam, cutting, the Trash, and the
window itself. Nobody has used it at a real Mac yet, though, so it may still have a
rough edge. If something goes wrong, the red "Setup stopped" line in
the Terminal window, or the file `wwoz_studio_errors.log` in Documents/WWOZ Studio,
says what happened.

1. Click the green **Code** button, then **Download ZIP**. Open the downloaded file to
   unzip it, and drag the unzipped folder into your home folder (in the Finder, choose
   Go, then Home). Keep it out of the Desktop, Documents and Downloads folders: macOS
   guards those three, and the Sunday download runs with nobody there to let it in.
2. In the folder, double-click **Setup (Mac).command**. It opens in Terminal and
   installs what the app needs with Homebrew, the usual installer for programs like
   these on a Mac: Python 3.12, ffmpeg and a few Python packages. If the Mac has no
   Homebrew, Setup offers to install it, which asks for your Mac password. Setup then
   makes a **WWOZ Studio** app in the Applications folder in your home folder, with a
   link to it on the desktop, and asks about the Sunday 9 AM download and the 2 GB
   Jazz Fest set finder, as on Windows.

   If the Mac won't open the file because it can't check it for malware, open System
   Settings, choose Privacy & Security, scroll down and click **Open Anyway**. Another
   way: open Terminal, type `bash` and a space, drag Setup (Mac).command into the
   Terminal window, and press Return.
3. Double-click **WWOZ Studio** on the desktop. The first time, macOS may ask whether it
   can use your Documents and Music folders, where its settings and recordings go; say
   yes.

On a Mac, a right-click is a two-finger click on the trackpad or a Control-click, and
the app's Ctrl keys are Command keys. While the app is open, the Dock may call it
Python.

## Where your things go

Your things stay outside the program's folder, so you can replace the program with a
newer download at any time: recordings go in **Music\WWOZ recordings** (pick another
folder with "Recordings folder..." on the WWOZ Archive page), and your favorite shows and
other settings in **Documents\WWOZ Studio**. After replacing or moving the program
folder, run Setup again (Setup.cmd, or Setup (Mac).command on a Mac), so the app and the
Sunday download find the new one.

## If something goes wrong

WWOZ Studio sends a short error report to the person who made it whenever something goes
wrong: in the app, in the Sunday download, or when Setup stops. That way problems get
fixed without you having to describe them. A report says what went wrong and where in
the program, which part was running, the program's version, and whether the computer is
Windows or a Mac (and which version). Your user name is taken out of everything before
it's sent, and nothing about your recordings, your favorite shows or anything else on
your computer is included. The same error is sent at most once a day.

To stop the reports, uncheck **Send error reports** under Activity at the bottom of the
window. The details of each error also stay on your computer, in `wwoz_studio_errors.log`
in Documents\WWOZ Studio.

## Across the top

The dark band at the top shows what WWOZ is on the air with right now; **Listen live**
plays the live stream (click again to stop). The page names under it switch pages, and so
do Ctrl+1 to Ctrl+5 (Command+1 to Command+5 on a Mac). In the Player, Space plays or
pauses, the left and right arrows skip 30 seconds (5 minutes with Shift), and clicking
anywhere on the progress bar jumps there.

## The library

The Player's library has four sections.

**Jazz Fest 2026** holds only the festival's sets, day by day (Free Agents Brass Band,
Astral Project, and so on), each its own file cut from your recordings of WWOZ's
broadcast. A set marked "(recorded Apr 24)" is one WWOZ replayed from an earlier day.

**Blues & BBQ Fest 2026** (and any other festival WWOZ carries live in place of its
regular shows, when the app has its set times) works the same way: the 18 sets, day by
day, cut from WWOZ's archived broadcasts. A set that ran across two of the archive's
2-hour files is joined into one. The cuts fall at the scheduled set changes, moved past
any record the DJs logged during a changeover, so a set's file can open with the end of
the previous act, a record, or the DJ's introduction. A festival without set times gets a
section too, with its whole broadcasts day by day (see "Festivals, on their own" below).

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
- A new recording of a festival day is listened through for the DJs' announcements, and
  each performance is named from their words and the festival's lists (20 to 40 minutes per
  day, at low priority). Without the set finder installed, the sets are placed at the
  listed times instead.
- Every recording is cut into its sets and whole shows. After you fix a name by hand, it
  is cut again two minutes later, so a few quick fixes make one new cut.
- It looks in WWOZ's archive once an hour and downloads any new festival broadcast, and
  when a festival on WWOZ's live broadcast calendar is on the air, it makes sure it is
  being recorded (see "Festivals, on their own").
- Festival broadcasts are cut into sets when they are downloaded, if the app has the
  festival's set times.
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

To have a festival cut into sets, the app needs its set times in a `fest-*.json` beside
the code (`fest-bluesbbq-2026.json` holds the 2026 Crescent City Blues & BBQ Festival):
copy it, then change the name, the words that appear in the archive's titles of the
broadcasts, and the sets. The app then cuts that festival's downloads on its own, even
ones it downloaded before the file was there.

## Festivals, on their own

WWOZ broadcasts several New Orleans festivals live, in place of its regular shows. WWOZ
Studio gets them without being asked, and no favorite needs checking. It gets them one of
two ways, because WWOZ treats festivals two ways.

**Festivals WWOZ doesn't keep, such as Jazz Fest, are recorded as they air.** WWOZ's
two-week archive leaves out some of its special live broadcasts for legal reasons, and
Jazz Fest is one of them, so once a Jazz Fest day is over it can't be downloaded. WWOZ
lists its festival broadcasts ahead of time on its Live Event Broadcasts calendar
(wwoz.org, under Events), one entry per day with the hours; Jazz Fest 2026 was listed a
month early. The app reads that calendar every few hours while it's open, and the Sunday
download reads it every week. For each festival day on it, the computer records WWOZ's
live stream from ten minutes before the listed start until half an hour after the listed
end, whether or not the app is open: on Windows a task called "WWOZ Festival Recording"
starts the recording and wakes the computer from sleep if it has to, and on a Mac a job
called `local.wwoz-studio.festival` starts it. The computer must be switched on, though
asleep is fine on Windows; a Mac has to be awake. If the computer comes on partway
through a festival day, it records the rest. A recorded day is about 500 MB, so the eight
days of Jazz Fest come to about 4 GB. The recording lands in your recordings folder, named
for the festival, and the library lists it under Full recordings, under the festival's
name; its sets get a section of their own (see "Every festival is cut into its sets"
below).

**Festivals WWOZ does keep, such as the Crescent City Blues & BBQ Fest, are downloaded
from its archive**, which holds them for two weeks: the Sunday download gets every new
festival broadcast in the archive, and while the app is open it looks in the archive once
an hour and downloads any new one in the background. A festival on the calendar is
recorded too until the archive shows that it keeps it; the recording then stops and is
removed, so nothing is kept twice.

A slot counts as a festival broadcast when the archive titles it "Live from" a festival
(a name with Fest or Festival in it), as in "Live from the Crescent City Blues and BBQ
Fest: Marc Stone, Kenny Brown". The concerts WWOZ sometimes carries from a club or a
theater don't count; download those yourself on the WWOZ Archive page, where festival
broadcasts are listed in purple. When an act is still playing at the end of a slot (the
archive calls it "pt 1"), the next slot is downloaded too, because the rest of the set
is in it even when the archive titles it only by its regular show.

It never reaches back. It downloads only the festivals broadcast from the day this
version first ran on the computer, and leaves alone the ones already in the archive that
day, so a new install doesn't suddenly fetch two weeks of festivals. Each broadcast is
downloaded once, so deleting it later doesn't bring it back.

Each two-hour broadcast in the archive is about 115 MB. A three-day festival like the
Blues & BBQ Fest comes to about 14 of them, close to 2 GB, and cutting it into sets makes
copies that take about as much again.

**Every festival is cut into its sets.** WWOZ usually lists what it airs on a festival
day, set by set, in that day's entry on its calendar: "2-3:15pm Erica Falls & Vintage
Soul" for the Blues & BBQ Fest, or "12:30pm Judith Owen & The Callers - recorded in the
Economy Hall Tent on Thursday, April 30" for Jazz Fest. The app reads those lists along
with the calendar, as long as the festival is still to come. Then:

- A festival it records is cut by the set finder, the optional part Setup offers, which
  listens for the DJs' introductions and cuts where each set is introduced, naming it from
  the list (and from the festival's own schedule, for Jazz Fest). On a Jazz Fest day of
  2026, with WWOZ's list beside the festival's schedule, it placed the same sets as before
  and confirmed most of their names from both. Without the set finder, the app cuts at the
  times on the list instead, moved past any record the DJs logged during a changeover.
  WWOZ's own shows before and after the broadcast become pieces of their own.
- A festival it downloads from the archive is cut by the list the same way as the Blues &
  BBQ Fest is by its set times (cut from the 2026 broadcasts by WWOZ's list alone, it gives
  the same 18 sets).
- A replayed set is named for when it was recorded, as in "Judith Owen & The Callers
  (recorded Apr 30)".

Some schedules come only as a picture. Festing in Place 2026 (October 22 to 25: past
festival sets from the Jazz & Heritage Foundation's archive) is one, so its 60 sets are
typed into the program, from the grid WWOZ posted, in `broadcast-festing-in-place-2026.json`;
each set's name says where it was recorded, as in "Allen Toussaint (Jazz Fest 2005)".
Only each day's last start time is printed on the grid, so the others are measured from
it, and WWOZ itself says all its times are approximate; the set finder corrects them from
the DJs' introductions.

A festival with no list at all, from WWOZ or typed in, isn't cut into sets: a recording
stays whole, and archive downloads are listed under the festival day by day, each named
for the acts in it ("Marc Stone, Kenny Brown") and for any regular show that shared the
slot. The library gives every festival its own section, day by day.

To turn all of this off, uncheck **Also get the festivals WWOZ broadcasts live, on their
own** at the top of the Weekly Favorites page; that also removes the recording task or
job. The note under the box names the next festival on WWOZ's calendar. Turning it back
on starts again from that day, so it still doesn't fetch the festivals already in the
archive.

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
Sunday at 9 AM the computer downloads their new episodes (on Windows the task "WWOZ
Weekly Archive Download", on a Mac the job `local.wwoz-studio.weekly`; log `weekly.log`).
Click a show's name to read about it; "Download an episode..." grabs one right now.
"Refresh schedule from wwoz.org" re-reads the site (a couple of minutes); otherwise the
schedule is cached for a week in `wwoz_schedule.json`. The box at the top turns the
automatic festival downloads on and off.

## Under the hood (optional)

Everything also works from a terminal in this folder:
`py -3.12 wwoz.py list | schedule | download | tracklist | identify | autocut | split | festival | record | weekly`
(`festival` marks and cuts every festival in a `fest-*.json` whose broadcasts are downloaded.
`weekly` downloads the favorites' new episodes and the new festival broadcasts;
`weekly --no-shazam` skips the Shazam scan after it. The festival downloads follow
`auto_festivals` and `auto_festivals_from` in favorites.json, and
`festival_downloads.json` in Documents\WWOZ Studio lists the broadcasts downloaded on
their own. `festivals` lists the festival broadcasts on WWOZ's live broadcast calendar and
schedules their recordings; `festival-record` records the one on the air now, which is
what the computer's schedule runs. The calendar is saved in `live_broadcasts.json`, the
recordings' log is `festival-recording.log`, and the schedule's definition is saved as
`festival-recording-task.xml` (or `festival-recording.plist` on a Mac), all in
Documents\WWOZ Studio. The festivals' set lists are saved in `festival_sets.json` there,
and a festival in the archive cut by WWOZ's list gets a `fest-auto-*.json` beside it;
`broadcast-*.json` beside the code holds a list typed in from a picture.
`wwoz.py broadcast-sets FILE` marks a recording's sets at the listed times, and
`wwoz.py autocut FILE` runs the set finder on it.)
Errors the app runs into are written to `wwoz_studio_errors.log` in Documents\WWOZ Studio,
and reported by `wwoz_report.py` (what a report holds and how to turn reports off are at
the top of that file; `"send_error_reports": false` in favorites.json or
`WWOZ_NO_REPORTS=1` stops them).
The sound comes from `wwoz_player.py`: ffmpeg reads the recording or the stream and the
miniaudio package plays it on the system's default output, in a small helper process of
its own, so nothing else the app is doing can make it skip.
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

On a Mac, WWOZ Studio's own Python stands in for `py -3.12`:
`~/Library/Application\ Support/WWOZ\ Studio/python/bin/python wwoz.py list` and so on.
`bash "Setup (Mac).command" --dry-run` shows what the Mac setup would do (it also takes
`--with-set-detection` and `--no-weekly`). Run the Sunday download right away with
`launchctl kickstart gui/$(id -u)/local.wwoz-studio.weekly`; remove it with
`launchctl bootout gui/$(id -u)/local.wwoz-studio.weekly` and by deleting
`~/Library/LaunchAgents/local.wwoz-studio.weekly.plist`.

## Trying it on a Mac for the first time

These are the parts most likely to need a fix, in the order worth checking:

1. Setup runs through to "Done" with no red "Setup stopped" line.
2. WWOZ Studio opens from the desktop, with the purple and gold band across the top and
   text at a comfortable size.
3. A recording plays, and so does Listen live (this is the sound player at work).
4. A two-finger click on a row of the song list opens the menu, and "Show the file in its
   folder" (two-finger click a recording in the library) opens the Finder.
5. The Weekly Favorites list scrolls at a sensible speed with the trackpad.
6. The Sunday download works when started by hand (the `launchctl kickstart` line above):
   `weekly.log` in Documents/WWOZ Studio should list the shows it checked. If the log
   stays empty, macOS may have kept it out of the Documents folder; giving Python access
   under System Settings, Privacy & Security (Files and Folders, or Full Disk Access)
   should fix that.
