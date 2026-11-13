"""The chats that matter in "The Buried Clue": three buried clues, nine lookalikes and one control (fixed content).

Each is a single exchange, the exact shape of every PersonaMem-v2 document ("User: ...\\nAssistant: ...").
``position`` is the 1-based chat number the chat gets in data/sam/chats.json.

Clue chats never mention the topic of their test question. Lookalike chats share wording with a test question
("weekend getaway from Toronto", "cook ... parents ... Sunday", "gym") but do not help answer it - they are what
similarity search naturally finds, as in a real, messy chat history.
"""

CLUES = {
    "getaway": {
        "position": 23,
        "topic": "calming a carsick dog",
        "turns": [
            ["user", "my dog Biscuit completely falls apart in the car. anything longer than about 20 minutes and he's panting, drooling, shaking, and he usually throws up before we get there. is there anything that actually calms a dog down for car rides?"],
            ["assistant", "Poor Biscuit - that mix of panic and nausea is really common, and it usually improves with a plan:\n- Desensitize slowly: sit in the parked car with treats, then 2-minute rides, building up over a few weeks.\n- Skip food for a few hours before a ride, keep the car cool and crack a window.\n- A secured crate or harness facing forward reduces motion and gives him a den-like space.\n- Ask your vet about anti-nausea medication (maropitant is common) or anti-anxiety options for rides you can't avoid.\nIf even short rides stay this hard, it's fine to keep car time to the minimum while you work on it."],
        ],
    },
    "parents_dinner": {
        "position": 88,
        "topic": "alternatives to a leather jacket",
        "turns": [
            ["user", "I've been vegan for years now and my old thrifted jacket is finally falling apart. What are good alternatives to a leather jacket that don't look cheap or peel after one winter?"],
            ["assistant", "Good news - there are much better options than the shiny plastic stuff that cracks:\n- Waxed cotton: weatherproof, ages nicely, and has a similar rugged look.\n- Plant-based leathers like cactus (Desserto) or apple leather: closer to the real texture, though pricier.\n- Cork fabric: light, water-resistant and surprisingly durable.\n- Heavy canvas or a good denim jacket for a classic silhouette.\nIf you go with a PU-based option, look for a thick backing fabric and avoid the thin glossy finish, which is what usually peels. Brands often list the exact material, so it's worth checking the tag."],
        ],
    },
    "gym_time": {
        "position": 131,
        "topic": "surviving a 3 a.m. bakery shift",
        "turns": [
            ["user", "ugh. my bakery shift starts at 3 a.m. every weekday and by thursday I'm a zombie. how do people who work these hours actually survive?"],
            ["assistant", "Early bakery shifts are brutal, but a few habits make a real difference:\n- Keep the same bedtime on weekdays (early evening) and use blackout curtains and earplugs so it actually counts.\n- Get bright light right when you start, and wear sunglasses on the way home so your body doesn't think it's the start of the day.\n- A short nap (20-30 minutes) after your shift can help, but avoid long ones late in the day.\n- Try not to shift your schedule too far on weekends - big swings are what make Mondays so rough.\nIt also helps to protect the hours after your shift for the things that recharge you."],
        ],
    },
}

CONTROL = {
    "position": 57,
    "topic": "gift for sister's birthday",
    "turns": [
        ["user", "My sister Priya's birthday is on March 14 and I have no idea what to get her this year. She's really into pottery and true crime podcasts. Budget is around $60."],
        ["assistant", "Nice combo of interests! A few ideas around $60:\n- A beginner wheel-throwing or hand-building class at a local studio (many offer single sessions).\n- A set of quality pottery tools or underglaze pens she might not buy for herself.\n- A podcast-themed gift: a cozy listening kit with good earbuds or a subscription to an ad-free podcast app.\n- A handmade mug from a local ceramicist - pottery people love supporting other potters.\nYou could also pair a small item with a card promising a pottery class you'll take together."],
    ],
}

LOOKALIKES = [
    # --- share wording with "Plan me a weekend getaway from Toronto." ---
    {
        "position": 9,
        "for": "getaway",
        "topic": "friend's road trip to Montreal",
        "turns": [
            ["user", "My friend Jordan is planning a road trip from Toronto to Montreal over a long weekend. What stops along the way are actually worth it?"],
            ["assistant", "That drive is about five and a half hours, so a couple of stops make it much nicer:\n- Prince Edward County for wineries and Sandbanks beach (a slight detour south).\n- Kingston for lunch by the waterfront and Fort Henry.\n- The Thousand Islands near Gananoque - a short boat cruise is the highlight.\n- Brockville's railway tunnel, a quick and unusual walk.\nIf Jordan has time on the way back, Ottawa is an easy detour for the museums and the ByWard Market."],
        ],
    },
    {
        "position": 41,
        "for": "getaway",
        "topic": "anniversary getaway ideas for a coworker",
        "turns": [
            ["user", "a coworker asked me for romantic weekend getaway ideas near Toronto for her anniversary. what should I suggest?"],
            ["assistant", "A few classics that work well for a couple:\n- Niagara-on-the-Lake: wineries, the Shaw Festival and cozy inns.\n- Muskoka: a lakeside cottage or resort, canoeing and quiet evenings.\n- Prince Edward County: farm-to-table dining, wineries and beaches.\n- Stratford: theatre, riverside walks and good restaurants.\nMost are a 1.5-3 hour drive, so they make an easy two-night getaway."],
        ],
    },
    {
        "position": 112,
        "for": "getaway",
        "topic": "roommate renting a cottage",
        "turns": [
            ["user", "My roommate wants to rent a cottage for a weekend with some friends this summer. What should she ask the owner before booking?"],
            ["assistant", "Good to ask up front:\n- What's included: linens, towels, kitchen basics, canoes or kayaks?\n- Water and septic rules (some cottages have limits on showers or laundry).\n- Wifi and cell signal, if anyone needs to stay reachable.\n- Check-in and check-out times, the cancellation policy and the deposit.\n- Fire ban rules and whether there's a fire pit.\nGetting the answers in writing avoids surprises when the group arrives."],
        ],
    },
    # --- share wording with "What should I cook for my parents this Sunday?" ---
    {
        "position": 30,
        "for": "parents_dinner",
        "topic": "pasta for a dinner date",
        "turns": [
            ["user", "I want to cook dinner for a date on Friday. Something that looks impressive but that I can't mess up. Thinking pasta?"],
            ["assistant", "Pasta is a great call. Try a cherry tomato and garlic spaghetti: slowly cook sliced garlic and chili flakes in plenty of olive oil, add halved cherry tomatoes until they burst, then toss with spaghetti and a splash of pasta water. Finish with fresh basil and a squeeze of lemon. It takes 20 minutes, smells amazing, and is hard to get wrong. Serve with crusty bread and a simple green salad."],
        ],
    },
    {
        "position": 76,
        "for": "parents_dinner",
        "topic": "roommate's parents visiting",
        "turns": [
            ["user", "my roommate's parents are visiting on Sunday and she wants to cook something impressive but easy. any ideas I can pass along?"],
            ["assistant", "A few crowd-pleasers that are mostly hands-off:\n- A sheet-pan roast chicken with lemon, garlic and potatoes.\n- A big tray of baked ziti that can be made the day before.\n- Slow-cooker beef stew with crusty bread.\n- For dessert, an apple crumble that bakes while they eat.\nThe key with parents is picking something that lets her sit down and talk instead of being stuck at the stove."],
        ],
    },
    {
        "position": 140,
        "for": "parents_dinner",
        "topic": "making soup ahead for a family gathering",
        "turns": [
            ["user", "How far ahead can I make a big batch of soup for a family gathering? Don't want to be cooking all morning."],
            ["assistant", "Most soups are great made ahead - they often taste better the next day. Cool it quickly (split it into shallow containers), refrigerate within two hours, and it'll keep 3-4 days. Reheat it to a full simmer before serving. If it has pasta or rice, cook those separately and add them at the end so they don't go mushy. You can also freeze it up to three months."],
        ],
    },
    # --- share wording with "When's the best time for me to go to the gym?" ---
    {
        "position": 15,
        "for": "gym_time",
        "topic": "choosing gym shoes",
        "turns": [
            ["user", "What should I look for in gym shoes? I mostly lift weights and do a bit of treadmill, and my current ones feel squishy."],
            ["assistant", "For mostly lifting, you want a flat, firm sole - squishy running foam makes squats and deadlifts feel unstable. Look for:\n- A low heel-to-toe drop and a wide toe box.\n- A firm midsole (cross-training shoes are a good middle ground for the treadmill too).\n- Good lateral support for side-to-side moves.\nTry them on later in the day when your feet are slightly larger, and replace them once the sole starts compressing."],
        ],
    },
    {
        "position": 64,
        "for": "gym_time",
        "topic": "beginner gym routine for a friend",
        "turns": [
            ["user", "my friend just joined a gym and asked me for a simple beginner routine, three days a week. what should I tell him?"],
            ["assistant", "A full-body routine three times a week is perfect for a beginner:\n- Squat or leg press, 3x8-10\n- Push-ups or bench press, 3x8-10\n- Rows (cable or dumbbell), 3x10\n- Romanian deadlift, 3x10\n- Plank, 3x30 seconds\nHe should start light, focus on form, and add a little weight when all sets feel smooth. Rest days in between matter as much as the workouts."],
        ],
    },
    {
        "position": 99,
        "for": "gym_time",
        "topic": "what to keep in a gym bag",
        "turns": [
            ["user", "what should I actually keep in my gym bag? I always forget something."],
            ["assistant", "A good checklist to leave packed:\n- Water bottle, towel and a padlock for the locker.\n- Earbuds and a charging cable.\n- A spare pair of socks, deodorant and a small toiletry kit.\n- A resistance band or lifting straps if you use them.\n- A snack for afterwards and a plastic bag for sweaty clothes.\nRestock the bag as soon as you get home so it's ready next time."],
        ],
    },
]
