/* =====================================================================
   CONTENT FILE — every word on the site lives here.
   ---------------------------------------------------------------------
   Edit freely. A few rules so nothing breaks:
     • Keep the quotes around each piece of text. If your text needs an
       apostrophe, either use ’ (curly) or write it inside "double quotes".
     • Every item in a list ends with a comma.
     • *stars* around words make them italic, **double stars** make them
       bold. A line break inside a paragraph: write \n
     • Gujarati lines are romanized; the meaning is in a comment next to
       each one (comments start with // and are never shown on the site).
   After editing, just refresh the page. No build step.
   ===================================================================== */

window.CONTENT = {

  /* ------------------------------------------------------------------
     SETTINGS
     ------------------------------------------------------------------ */
  settings: {
    herName: "Radha",
    fromName: "Ash",

    // When the site opens (India time). Don't change the "+05:30" part.
    birthday: "2026-10-12T00:00:00+05:30",

    // When you arrive for Navratri.
    navratri: "2026-10-17T00:00:00+05:30",

    // Secret link to see everything early: yoursite.vercel.app/?preview=ash
    previewKey: "ash",

    // YOUR WhatsApp number: country code + number, digits only, no + or spaces.
    // Example: "919876543210"
    whatsappNumber: "91__________",

    // Text that goes in front of her wish in WhatsApp.
    wishPrefix: "🎂 My birthday wish: ",
  },

  /* ------------------------------------------------------------------
     FILES — the site adapts to what's here.
     Photos: run the photo script (see README) and it fills in
     assets/photos/manifest.js automatically. Captions below are matched
     to photos in order (1st caption → 1st photo, …).
     ------------------------------------------------------------------ */
  audio: {
    main:   { src: "assets/music/her.mp3",                 title: "Her — JVKE" },
    letter: { src: "assets/music/say-you-wont-let-go.mp3", title: "Say You Won’t Let Go — James Arthur" },
    voice:  { src: "assets/audio/voice-note.m4a" },
  },

  /* ------------------------------------------------------------------
     1. COUNTDOWN (shown until her birthday begins)
     ------------------------------------------------------------------ */
  countdown: {
    eyebrow: "for RaRa",
    line: "something is blooming for you, RaRa…",
    sub: "come back when the lilies open 🤍",
    labels: { days: "days", hours: "hours", minutes: "minutes", seconds: "seconds" },
    dateLine: "12 · 10 · 2026",
  },

  /* ------------------------------------------------------------------
     2. PASSWORD GATE
     (The answer is NOT written anywhere — only a scrambled version of it
      lives in app.js, so peeking at the code won't give it away.)
     ------------------------------------------------------------------ */
  gate: {
    eyebrow: "one little question first",
    title: "When did we stop pretending?",
    prompt: "Type the date. Any way you like.",
    placeholder: "dd / mm",
    button: "Open",
    hintButton: "need a hint?",
    hint: "the 3 a.m. FaceTime when we both stopped pretending 🤍",
    wrong: [
      "Hmm… that's not it, RaRa 🙈",
      "Overthinking again? Breathe. Think 3 a.m. 🤍",
      "Ash is pretending to be offended right now 😤",
      "So close to the door… try once more 🌷",
      "Aavu kai nai yaad? 🥲",   // "you don't remember something like this?"
    ],
    success: "there you are 🤍",
    // Shown if she opens the site again after already unlocking it once.
    welcomeBack: "Welcome back, RaRa",
    welcomeBackButton: "Open again",
    // Shown only if her phone needed one more tap to start the music.
    tapToBegin: "tap anywhere to begin ♪",
  },

  /* ------------------------------------------------------------------
     3. OPENING
     ------------------------------------------------------------------ */
  opening: {
    kicker: "12 October 2026",
    title: "Happy 19th Birthday,",
    name: "Radha",
    nicknames: ["RaRa", "radhsss", "raddy plays baddy 🏸"],
    gujarati: "Janmdivas ni khub khub shubhechha 🤍", // "very, very happy birthday"
    sub: "I made you a little place on the internet. Go slow — it's all for you.",
    scrollCue: "scroll softly",
  },

  /* ------------------------------------------------------------------
     4. OUR STORY — timeline
     ------------------------------------------------------------------ */
  story: {
    eyebrow: "chapter one",
    title: "Our story",
    intro: "Funny how the biggest thing in my life started as a coincidence.",
    entries: [
      {
        date: "years ago",
        title: "Technically, we'd already met",
        text: "You're my cousin's cousin — the kind of family connection nobody can ever explain properly at weddings. We'd met a couple of times. Polite hellos. It didn't mean anything then. (The universe was clearly just warming up.)",
      },
      {
        date: "11 May 2026",
        title: "Same house, same days, pure chance",
        text: "We both landed at my mama's place — which is also your bua's place — at the exact same time. Ten, twelve days under one roof. Nobody planned it. I've stopped believing it was random.",
      },
      {
        date: "a Sunday morning",
        title: "Worli promenade",
        text: "Our first real conversation, just us. You told me how much you overthink and how heavy it gets sometimes. You were so honest, and I remember thinking: oh. *Oh.* That's the morning it clicked for me.",
        gujarati: "tyaare j khabar padi gayi ti", // "that's when I knew"
      },
      {
        date: "22 August 2026 · 3 a.m.",
        title: "The FaceTime",
        text: "Me at home in Mumbai, you at Janki's place, both of us very awake for 3 a.m. I finally said it. And you just said — basically — \"wasn't it obvious? from how close I was with you and how much we talked?\" Easiest, scariest, best moment of my life.",
        highlight: true,
      },
      {
        date: "now",
        title: "Mumbai ↔ Ahmedabad",
        text: "You — Bhavnagar girl, studying in Ahmedabad. Me — Mumbai. A few hundred kilometres that feel like nothing on a call and like everything when I want to hold your hand.",
      },
    ],
    map: {
      title: "the distance between us",
      from: "Mumbai",
      fromNote: "me",
      to: "Ahmedabad",
      toNote: "you",
      home: "Bhavnagar",
      homeNote: "where you're from",
      distance: "~500 km",
      caption: "~500 km apart, and still the closest person to me.",
    },
  },

  /* ------------------------------------------------------------------
     5. HER NOW — photo gallery (captions in photo order)
     ------------------------------------------------------------------ */
  now: {
    eyebrow: "chapter two",
    title: "You, right now",
    intro: "Nineteen looks unfairly good on you.",
    captions: [
      "this smile. this exact one. I'd travel 500 km for it (I will)",
      "how is anyone this pretty without even trying?",
      "in red. I rest my case. 🌹",
      "the look you give me right before you say something savage",
      "my favourite view on my whole phone",
      "you, being the main character. as usual.",
      "I zoom in on this one more than I'll ever admit",
    ],
  },

  /* ------------------------------------------------------------------
     6. LITTLE RADHA — childhood gallery
     ------------------------------------------------------------------ */
  little: {
    eyebrow: "chapter three",
    title: "Little Radha",
    intro: "Before the badminton smashes and the 3 a.m. calls, there was this tiny menace.",
    captions: [
      "already plotting something",
      "the original RaRa 🎀",
      "those cheeks should be illegal",
      "overthinking since day one",
      "future badminton champion warming up",
      "this face = \"chup thai jaa\" in baby language",
      "tiny, but clearly in charge",
      "someone give her a medal for cuteness",
      "the eyes were always this dangerous",
      "if I'd met you then I'd have shared my chocolate (maybe)",
    ],
  },

  /* ------------------------------------------------------------------
     7. THE BLOOPER REEL — funny section
     ------------------------------------------------------------------ */
  bloopers: {
    eyebrow: "intermission",
    title: "The blooper reel",
    intro: "Every love story needs a little roasting. Purely affectionate. Mostly.",
    captions: [
      "Exhibit A: no further questions, your honour",
      "Exhibit B: the face of someone who just ate an ice cube for fun",
      "Exhibit C: and I still chose you 😌",
    ],
    iceCubes: {
      title: "A professional ice-cube eater",
      text: "Some people have dessert. Radha has… frozen water. Crunch crunch. I've accepted it. I've stopped asking why. I just love you and keep the freezer full. 🧊",
    },
    chup: {
      title: "Every. Single. Time.",
      her: "chup thai jaa 😒",                                   // "shut up"
      me: "hu chup thai jais toh tu koni saathe vaat karse? 🙃", // "if I go quiet, who will you talk to?"
      note: "— undefeated comeback, still unanswered",
    },
  },

  /* ------------------------------------------------------------------
     8. REASONS I LOVE YOU — tap-to-flip cards
     front = short label on the closed card, back = the reason
     ------------------------------------------------------------------ */
  reasons: {
    eyebrow: "chapter four",
    title: "Reasons I love you",
    intro: "Tap a card to turn it over.",
    allFlipped: "…and about a thousand more I'm saving for in person. 🤍",
    cards: [
      { front: "your face when you talk",  back: "The way your whole face tells the story before your words do. The eyebrows. The eye-rolls. I could watch you explain anything." },
      { front: "your hair",                back: "Your hair. Open, tied, messy at 3 a.m. — every version. I notice every single time." },
      { front: "your eyes",                back: "Your eyes. They're soft and dangerous at the same time and I have no defence against them." },
      { front: "raddy plays baddy",        back: "You're genuinely brilliant at badminton. Watching you play is the only time I'm happy to lose." },
      { front: "worli, sunday",            back: "That Sunday at Worli you let me into your head — the overthinking, all of it. Thank you for trusting me with that." },
      { front: "\"wasn't it obvious?\"",   back: "You made the scariest confession of my life feel easy. Obvious. Like coming home." },
      { front: "your laugh",               back: "Your laugh, especially the one you try to hide. It never works. I always hear it." },
      { front: "how you care",             back: "You care so deeply about the people you love — it's quiet, but it's in everything you do." },
      { front: "3 a.m. you",               back: "Sleepy 3 a.m. you, still on the call, refusing to hang up first. Best part of my day (night)." },
      { front: "you, just you",            back: "Because being yours feels like the most natural thing I've ever done. Tu j che, bas. 🤍" }, // "it's just you, that's all"
    ],
  },

  /* ------------------------------------------------------------------
     9. OPEN WHEN… letters
     ------------------------------------------------------------------ */
  openWhen: {
    eyebrow: "for later",
    title: "Open when…",
    intro: "Four little envelopes for the days I can't be there. Open one whenever you need it.",
    close: "fold it back up",
    letters: [
      {
        label: "you miss me",
        title: "Open when you miss me",
        body: [
          "Hi. I miss you too — probably right this second, honestly.",
          "Close your eyes and think of the last time we laughed on call so hard we couldn't breathe. I'm still there. I'm always one ring away, and soon I won't even need the phone.",
          "Tane bau yaad karu chu. Every single day.", // "I miss you a lot"
        ],
        signoff: "always yours, Ash",
      },
      {
        label: "you're sad",
        title: "Open when you're sad",
        body: [
          "Hey, RaRa. Come here.",
          "You don't have to be okay right now. You don't have to explain it, fix it, or be strong about it. Just let it be heavy for a bit — I'm holding the other end.",
          "And when you're ready, call me. Even if you don't want to talk. I'll just stay on the line and tell you about my boring day until you smile.",
        ],
        signoff: "here, always — Ash",
      },
      {
        label: "you can't sleep",
        title: "Open when you can't sleep",
        body: [
          "It's late and your mind is doing that thing again, isn't it? Running through every conversation, every 'what if'.",
          "Here's what I know for certain: you are loved, you are enough, and nothing you're replaying right now is as big as it feels at this hour. Thoughts at 2 a.m. always lie a little.",
          "Breathe in for four, hold for four, out for six. Put one hand on your heart — that's me saying goodnight. Chinta na kar, hu chu ne.", // "don't worry, I'm here"
          "If it's still loud, text me. I don't mind being woken up by you. Ever.",
        ],
        signoff: "sleep soft, Ash",
      },
      {
        label: "you're angry at me",
        title: "Open when you're angry at me",
        body: [
          "Okay. Whatever I did… I'm sorry. (I'm probably also confused, but mostly sorry.)",
          "You're allowed to be mad. Be mad. Say \"chup thai jaa\". I'll even let you win this one — and you know how rare that is.",
          "Just don't go to sleep angry with me, okay? Call me and yell. Then let me make it right. I'd rather fix things with you than be right with anyone else.",
        ],
        signoff: "your slightly scared boyfriend, Ash",
      },
    ],
  },

  /* ------------------------------------------------------------------
     10. THE BIRTHDAY LETTER  (music switches to "Say You Won't Let Go")
     Each line in `paragraphs` appears one by one as she scrolls.
     ------------------------------------------------------------------ */
  letter: {
    eyebrow: "the letter",
    title: "For your 19th",
    salutation: "Dear RaRa,",
    paragraphs: [
      "Happy birthday, you menace. Nineteen! You're officially too old to be this cute and somehow you're getting cuter, which honestly feels like a personal attack.",
      "I wanted to give you something you could keep — not just a message that gets buried under a hundred others. So I built you this. Yes, I'm that boyfriend now. You did this to me.",
      "Do you know what's strange? I've known of you for years. Cousin's cousin, a face at family functions, a polite hi. And then May happened — the same house at the same time, completely by accident — and suddenly you were the most interesting person in every room.",
      "But it was that Sunday at Worli that really did it. The sea, the breeze, and you, telling me how much you overthink, how loud your head gets sometimes. You didn't have to tell me any of that. You did anyway. I don't think you know how much that meant to me — that you let me see the real you so early.",
      "Then came 3 a.m. on 22 August. My heart was going so fast I'm surprised you couldn't hear it over FaceTime. And you, completely calm: \"wasn't it obvious?\" I've replayed that moment more times than I'll ever admit. It *was* obvious. I just needed to hear you say it.",
      "Since then, everything has felt a little brighter. Even the distance. Five hundred kilometres sounds like a lot until I'm talking to you, and then it's nothing. Then it's just us.",
      "I love the way you talk with your whole face. I love your hair, your eyes, your laugh, the way you destroy people at badminton and then act innocent. I even love the ice cubes. (Okay, I tolerate the ice cubes. Lovingly.)",
      "And on the days your mind gets heavy — and I know there will be some — I want you to remember this: you never have to carry it alone again. Not the overthinking, not the bad days, not anything. Hu chu ne. I'm here. I'm staying.", // "I'm here, no?"
      "I don't know exactly what the future looks like. But I know I want you in all of it. Every birthday, every Navratri, every boring Tuesday, every 3 a.m. call that turns into 5 a.m.",
      "So here's my wish for you this year: that you see yourself the way I see you. Gentle and strong. Funny and brave. So, so loved.",
      "Happy 19th birthday, Radha. Thank you for being born, and thank you for choosing me back.",
    ],
    signoff: "Forever yours,",
    signature: "Ash 🤍",
  },

  /* ------------------------------------------------------------------
     11. NAVRATRI COUNTDOWN
     ------------------------------------------------------------------ */
  navratri: {
    eyebrow: "coming soon",
    title: "See you in Ahmedabad",
    line: "Five days after your birthday, I'm coming to play garba with you.",
    gujarati: "Taiyaar rehje — hu aavu chu 💃🏻", // "be ready — I'm coming"
    labels: { days: "days", hours: "hours", minutes: "min", seconds: "sec" },
    // Shown automatically once 17 October has arrived:
    afterTitle: "We made it 🤍",
    afterLine: "No more countdowns, no more 500 km. Just you, me, and garba till our feet hurt.",
  },

  /* ------------------------------------------------------------------
     12. MAKE A WISH
     ------------------------------------------------------------------ */
  wish: {
    eyebrow: "your turn",
    title: "Make a wish",
    intro: "Nineteen candles. Tap the cake to blow them out.",
    blowButton: "blow out the candles",
    afterBlow: "Now make a wish and send it to me.",
    placeholder: "I wish…",
    sendButton: "Send it to Ash",
    note: "(I promise to help make it come true.)",
  },

  /* ------------------------------------------------------------------
     13. FINALE
     ------------------------------------------------------------------ */
  finale: {
    eyebrow: "one last thing",
    intro: "Put your earphones in, if you can. This one's just my voice.",
    playLabel: "play",
    pauseLabel: "pause",
    line: "Happy birthday, RaRa. — Ash 🤍",
    afterLine: "Tu mari duniya che.", // "you are my world"
  },

  /* ------------------------------------------------------------------
     Little labels used around the site
     ------------------------------------------------------------------ */
  ui: {
    mute: "Mute music",
    unmute: "Play music",
    nowPlaying: "now playing",
    photoMissing: "photo coming soon",
    tapToClose: "tap anywhere to close",
  },
};
