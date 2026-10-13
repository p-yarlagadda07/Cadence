# Cadence

A login system that checks how you type your password, not just what you type.

Everyone has their own typing rhythm. How long you hold each key, how quickly
you move between certain letters, where you pause, how you use the Shift key.
Cadence learns that rhythm when you sign up, and on every login it checks both
the password and the rhythm. If someone else types your password correctly but
with a different rhythm, they don't get in.

This is called keystroke dynamics, a type of behavioural biometrics.

Built by Poojitha Yarlagadda ([@p-yarlagadda07](https://github.com/p-yarlagadda07)).

## Why I built it

I've been getting into cybersecurity, and most of what we do in college is
development, so I wanted a project that was actually about security. I also
wanted to understand behavioural biometrics properly, not just read about them.
The question that got me started was simple: if someone steals your password,
is there anything left that still proves it's you? It turns out the way you
type it is surprisingly hard to copy.

## Running it

You need Python 3.10 or newer.

```
pip install -r requirements.txt
python -m flask --app app run --port 5001
```

Then open http://localhost:5001. On a Mac, port 5000 is taken by AirPlay, which
is why this uses 5001. Use a laptop or desktop keyboard; phones don't report key
timings properly.

## How it works

**Capturing the rhythm.** The browser records the exact time every key goes
down and comes back up (`static/app.js`). Corrections aren't allowed, so
backspace, arrow keys or pasting throw the sample away, because a password
you fixed halfway through doesn't have your normal rhythm.

**Features.** For a password of n characters we get 2n - 1 numbers
(`rhythm.py`):

- hold time for each key
- time from pressing one key to pressing the next

The first version also used the time from releasing one key to pressing the
next. I dropped it because it's just the second one minus the hold time, so it
was counting the same information twice and giving some keys too much weight.

**Shift key.** For every capital or symbol, three more things get recorded: how
long before the letter you pressed Shift, how long after the letter you let go
of it, and whether you used left or right Shift. Most people are very
consistent about this and it varies a lot between people, so passwords with
capitals and symbols are much harder to fake. Using the other Shift key, or
Caps Lock instead of Shift, adds a fixed penalty to the score.

**Signing up.** You type the password 8 times. For each feature we store the
average and how much it usually varies (mean absolute deviation). If one sample
is way off from the rest, it gets dropped. The starting threshold is worked
out from how much your own samples disagree with each other.

**Logging in.** The new attempt is compared using scaled Manhattan distance:

```
distance = average over all features of |attempt - your average| / your usual variation
```

If the password is right and the distance is under your threshold, you're in.
Dividing by your usual variation matters, because a gap you're always
consistent on should count more than one that's all over the place.

I went with this method because
[Killourhy and Maxion (2009)](https://www.cs.cmu.edu/~keystroke/) compared 14
keystroke detectors on the same dataset and scaled Manhattan came out on top,
while being simple enough to explain in a few lines.

**Speed check.** Averaging every timing together has a weak spot. If someone
types with a similar pattern but noticeably faster, each gap only moves a
little, so the overall score can still pass. To catch that, the profile also
stores how long you usually take to type the whole password and how much that
varies. An attempt far outside your normal time is rejected no matter what the
rhythm score says.

The usual time comes from your last 8 successful logins, not from sign up. I
found that out the hard way: people type slowly and carefully while signing
up, then type normally afterwards, so the app kept thinking I was "too fast".
New accounts also get some extra speed leniency until they have 12 samples.
Blocked logins never update the profile, otherwise someone could slowly train
your account to accept them.

**Lockout.** After 5 failed logins in a row (wrong password or wrong rhythm)
the account locks for 15 minutes. Without this, someone who knows your
password could keep trying until one attempt happens to land close enough.
When you sign up you get a recovery code, shown once, which unlocks the account
straight away. Using it gives you a new one, so each code only works once. It's
stored hashed, like the password.

**Adapting.** Every successful login gets added to your profile (the last 15
are kept), so it keeps up if your typing slowly changes.

## Testing it properly

The Test tab is where you find out if it actually works. Type your password a
few times marked as "It's me", then give your password to friends and let them
try marked as "Someone else". It shows two numbers:

- **FRR (false reject rate):** how often you get locked out of your own account
- **FAR (false accept rate):** how often someone else gets in

Making it stricter lowers FAR but raises FRR, and the other way round. The
chart shows both across every threshold, and "Use best threshold" picks the
middle of the range with the fewest mistakes.

## Results

**Setup.** One account, mine, with a 9 character password: 1 capital letter,
1 special character and 2 numbers. I type capitals and symbols with the right
Shift key, on my laptop keyboard. After signing up I logged in normally a few
times so the profile could settle, then did 11 attempts as myself.

Four friends then tried my password, 4 to 5 attempts each, 20 attempts in
total. Two of them practised it a few times before their recorded attempts and
two went in cold. One friend also watched me type it first and tried to copy
my rhythm.

At a threshold of 1.95:

| | Attempts | Result |
|---|---|---|
| Me (FRR) | 11 | 1 blocked, 9% |
| Impostors (FAR) | 20 | 2 let in, 10% |

The two error rates cross at about 10%, which is the equal error rate.

![FRR and FAR across every threshold](screenshots/chart.png)

Impostor attempts on the Test page. Most score well above my usual range, and
one was caught by the speed check:

![Impostor attempts getting blocked](screenshots/attempts.png)

**How it got there.**

- The first version, without the Shift key or the speed check, had an equal
  error rate of about 29%. Some impostors were typing the same pattern as me,
  just faster, and the averaged score couldn't tell the difference.
- Adding the speed check stopped impostors who type at a very different pace.
- Recording Shift timing helped the most. In one test, an attempt with the same
  letter rhythm as me but a different Shift habit scored 2.68, while my own
  attempts usually land between 0.8 and 1.5.
- The first speed check was too strict and blocked me whenever I typed a bit
  faster than usual, so I loosened it after testing.

**What surprised me.**

- Watching someone type really does help copy them. One of the two impostor
  attempts that got in was from the friend who watched me first. Shoulder
  surfing isn't just about seeing the password.
- Short or common passwords are almost useless for this. When I tested with a
  plain English word, everyone typed it in the same quick burst and the system
  couldn't separate people at all. Capitals and symbols made a huge difference.
- My own typing varies more than I expected. Some days I'm just faster, which
  is why the speed check had to be loosened.

This is a small test with a handful of people, so the exact numbers would
change with more testers. A proper evaluation would use a public dataset like
the CMU keystroke benchmark.

## Project structure

```
app.py               Flask server and API
rhythm.py            feature extraction, matching, threshold
templates/index.html the page
static/app.js        key recording, charts, UI
static/style.css     styles
```

Data goes into a local SQLite file, `cadence.db`, which gets created on first
run. Passwords and recovery codes are stored hashed (werkzeug), never in plain
text.

## Limitations

- This is a demo. The login screen tells you whether it was the password or
  the rhythm that failed, which a real system shouldn't do since it helps an
  attacker.
- No sessions. Logging in shows the result but doesn't keep you signed in.
- The Test page doesn't count towards the lockout and shows whether each
  attempt would pass, so someone could practise on it. It's there for tuning
  and would be removed in a real deployment.
- Your rhythm can change with a different keyboard, an injury, or being half
  asleep, so this works best as an extra check alongside the password, not a
  replacement for it.
- Someone who records your typing (screen recording, a keylogger) could in
  theory replay your timings.

## Ideas for later

- Try other detectors (nearest neighbour, Mahalanobis distance, one-class SVM)
  and compare FAR/FRR
- Test on the public CMU keystroke dataset to get numbers from 51 typists
- Learn typing rhythm on free text, not just a fixed password
