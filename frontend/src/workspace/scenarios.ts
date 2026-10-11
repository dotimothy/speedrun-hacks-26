// Synthetic, multi-turn examples. Load one statement at a time so the review
// queue demonstrates additional detail and corrections as the call develops.
export const detailedScenarios = [
{
  "name": "Provided call: The neighbor is still inside",
  "turns": [
    "Oh god, okay, okay, I'm outside, I got out, but there's smoke in the hallway and I think it's coming from 6B, the lady with the oxygen tank, I banged on her door and she didn't answer.",
    "It's the brick building on Birch, um, Birch Court, I always blank on the number, it's 1400, 1400 Birch Court, apartment… I'm in 6A, the fire is in 6B. The stairs are full of smoke. I can see flames in her window from out here.",
    "I don't know, I don't know. My roommate is out here with me, that's two of us. The guy in 6C came down with his kid. But Mrs. Ellison, I don't think she came out. Her door was hot. There's a propane grill on the landing too, I almost tripped on it.",
    "I'm coughing, my eyes are burning, nobody's burned that I can see. The kid is crying but he's okay.",
    "Denise. Denise Alvarez. My phone is 555… hold on, 555 0151. Please just send them, the smoke is coming out of the third floor now, it's not just her apartment anymore."
  ]
},
{
  "name": "Provided call: Fall, chest, and a stroke",
  "turns": [
    "She's on the floor, she was fine, she was making tea, and then I heard the cup break. Mom? Mom, can you hear me? She's looking at me but she's not talking right. The left side of her face is… it's drooping, and she grabbed her chest before she went down.",
    "Her eyes are open. She's breathing, but it's loud, like she can't get a full breath. She fell against the counter. There's blood on her eyebrow. I don't know if it's her heart or a stroke or she just hit her head. She's sixty-eight.",
    "I'm at her house, not mine, I almost said my apartment. 77 Willow Lane. The blue house with the ramp. The front door is unlocked.",
    "Just me and my son. He's eight, he's in the living room, I told him to stay there. Nobody else is hurt. My name is Priya Shah. If you call back use my cell, 555 0129, the house phone doesn't work.",
    "I'm right here. Mom, they're coming. She just tried to sit up and she can't move her left arm."
  ]
},
{
  "name": "Provided call: She cannot remember the building",
  "turns": [
    "The apartments. There's smoke in the hallway, I got out, but her door was hot and she didn't answer. I don't know the number. I always come in through the garage.",
    "West side. I'm on the sidewalk. There's a car wash across the street, the blue one, and the building is gray. Flames are in a window up on the third floor. I'm not going back in.",
    "Okay. Okay. The sign on the corner says Birch Court. The other street is Holt Avenue. Birch Court and Holt.",
    "It's dark. The man from down the hall is yelling that it's fourteen hundred. 1400. 1400 Birch Court. I live in 6A. The hot door was the one next to mine. That's 6B. The woman with the oxygen machine.",
    "Four of us are out. Me, my roommate, that man, and his little boy. She didn't come out. There's a propane grill on the landing. I'm coughing. Nobody's burned. My name is Denise Alvarez, my phone is 555 0151."
  ]
},
{
  "name": "Provided call: She gives the wrong house",
  "turns": [
    "She's on the kitchen floor. Mom? Her face is drooping and she grabbed her chest before she fell. I don't know the address. I just drive here.",
    "I live at 15 Harbor Apartments. That's not here. This is her house. It's blue, it has a wheelchair ramp, and you pass the elementary school and turn left.",
    "Willow. Willow Lane. Her eyes are open. She's breathing, but it's loud, and there's blood on her eyebrow. She can't lift her left arm.",
    "The plaque by the ramp says 77. There's a bill on the table. 77 Willow Lane. The front door is unlocked.",
    "Me and my son. He's eight, he's in the living room. Nobody else is hurt. I'm Priya Shah. Call my cell, 555 0129. The house phone doesn't work. Mom, stay awake."
  ]
},
  {
    name: "Apartment fire · changing conditions",
    turns: [
      "There is a fire in an apartment at 418 Alameda Street. My callback is 555-0147. I am outside and safe. Nobody is hurt.",
      "It started about five minutes ago in the kitchen. Smoke is now spreading into the hallway. There are four people involved.",
      "Three people got out. I do not know whether the fourth person is still inside. We are at the rear entrance beside the alley, on the second floor landing.",
      "Correction: the entrance is on the east side, beside the blue gate. Everyone is now outside, including the fourth person.",
    ],
  },
  {
    name: "Medical call · symptoms and history",
    turns: [
      "My father has chest pain at 220 Cedar Street. My callback is 555-0182. I am somewhere safe. He is awake and breathing.",
      "He is 67 years old. The chest pain started ten minutes ago while he was sitting down. He has a history of asthma and takes medication.",
      "His condition is getting worse now.",
      "His chest pain is stronger than it was two minutes ago.",
      "The entrance is through the courtyard, apartment 4B on the first floor. I do not know the name of his medication.",
    ],
  },
  {
    name: "Traffic collision · vehicles and uncertainty",
    turns: [
      "There has been a traffic collision at Oak and Third. My callback is 555-0166. I am safe on the sidewalk. One passenger is injured.",
      "Two vehicles are involved: a blue sedan and a white van. The northbound lane is blocked and the road is wet. The crash happened about two minutes ago.",
      "Someone is trapped and cannot get out of a vehicle.",
      "The passenger is trapped in the front seat of the white van.",
      "There are three people outside. There is another person in the second car, but I do not know if they are hurt. The entrance to the parking lot is beside the bus stop.",
      "Sorry, the location is Oak and Fourth, not Third.",
    ],
  },
  {
    name: "Crime report · departure and direction",
    turns: [
      "Someone is breaking into a shop at 350 Pine Street right now. My callback is 555-0123. I am safe across the street. Nobody is hurt. I have not seen a weapon.",
      "The intruder is still here. The person is wearing a red jacket and a black backpack.",
      "Correction: the person has left. The break-in has stopped.",
      "The person ran north toward the station. I last saw them beside the bus shelter a minute ago.",
    ],
  },
  {
    name: "Gas smell · location and reported symptoms",
    turns: [
      "There is a gas smell inside the house at 610 Willow Road. My callback is 555-0154. I am outside and safe. Everyone is out of the house.",
      "One person says they feel unwell. I do not know whether it is related to the gas.",
      "My sister feels dizzy and has a headache. There are three people here. The gas smell started in the kitchen about five minutes ago.",
      "The entrance faces Willow Road beside the red gate. Nobody else has reported symptoms.",
    ],
  },
  {
    name: "Water rescue · visibility and last sighting",
    turns: [
      "A person is in trouble in the river near 80 River Road. My callback is 555-0178. I am safe on the bank. I can still see the person in the water.",
      "There is a strong current moving downstream. I can see the person near the second bridge pillar.",
      "Correction: I cannot see the person anymore.",
      "I last saw the person beside the second bridge pillar about thirty seconds ago. The entrance to the riverside path is beside the parking lot.",
    ],
  },
];
