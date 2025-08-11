# mantled-critters
This will house all of the upcoming project files and discoveries regarding Mantled Beasts/Protogen Systems. See www.mantledbeasts.com to start.

# Links and Stuff
https://www.printables.com/model/1111257-mantle-beast-mask - STL Mantled Beast Visor. Starting reference template.

https://youtu.be/xjmwgky0e2Q?si=_eidbPT9R8cHnfds - A DIY Project using a Raspberry Pi and an eye tracker with an OLED panel to display the pupil. Great way to introduce yourself into open source eye tracking.

https://github.com/JEOresearch/terminatorsunglasses - The repository for the above-mentioned eye tracker setup.

https://github.com/dimitrivlachos/Proto-OS - ProtoOS, Seems to not have been updated in the last few years but I reckon we can get a good head start using that software.

https://shop.pimoroni.com/collections/displays?page=1 - You want to know about displays? Anything that is shiny and pretty that would probably interest a Mantled Beast? Go check this out.

https://www.digikey.com/en/products/detail/raspberry-pi/SC0195(9)/12159401?gclsrc=aw.ds&gad_source=1&gad_campaignid=20228387720&gclid=Cj0KCQjwqebEBhD9ARIsAFZMbfz-UVNKifSWdpehyPJnD-BVP-z2VI05ALc8h2NcJ3Kw64STAc1txEIaApzTEALw_wcB - TensorFlow Lite object detection with Raspberry Pi4. Useful for on-board AI Object Detection.

https://www.furbittenstudios.com/ - Where I got all the Protogen pre-built stuffs. This helps cut down on the physical design and prototyping and allows me to learn sensor integration and LED matricies.

https://m.youtube.com/watch?v=uTMegWBuu_U&pp=0gcJCfwAo7VqN5tD - How to make a Protogen! I will be follow this guide to an extent.

# Upcoming Steps
- [x] Create ARKit face tracking/measurement application to allow us to extrapolate a .OBJ or .STL reference of our face.

- [ ] Utilize the exported file to create a reference that we can use the STL Mantled Beast Visor as an overlay. Scale to appropriate proportions.

- [ ] Create a shell to house the electronics. This can be printed in TPU or PETG/ABS. Need something that's resilient to heat and will not crack/destroy itself over a long period of time.

- [x] Buy the pretty color LED matricies to start testing expressions using ProtoOS as a launchpad.

- [ ] Learning how to vacuum form a 3D printed shell of the visor from concept to one solid piece.

- [ ] Double ear setup, because two ears means I can listen better! Probably... This will have to be custom designed using the pre-existing TPU ears.

# Long Term Steps
- [ ] Integrate open-source eye tracking into the system. Perhaps facial tracking, if at all possible.

- [ ] Install a voice modulator, something to allow us to fully immerse ourself into the environment.

- [ ] Install a VR headset, or something that can allow us to visually enhance what we're seeing in front of us, rather than looking through a dimmed plastic visor.
This may take some more research, as integrating a VR headset is not something you will commonly see. Perhaps understanding how fresnel/pancake lenses can come in handy here may help.

- [ ] Additional displays internal to the visor to show HUD systems with various real-time environment data based on on-board sensors.

- [ ] Additional camera/vision systems, separate to the VR headset idea, as an alternative, for situational awareness. Would a flexible display work inside the visor?

- [ ] Auditory enhancements for situational awareness. Built-in headset/speakers!

# Updates:
2025.08.11 - Protogen visor and various 3D printed parts from FurBitten Studios have arrived. I75W matrix driver board arrived, however the panels I ordered were WAY too large -- 128x64 panel resolution (320x160mm), when I should have ordered 64x32 (160x80mm), which Pimoroni does not have. New parts are on-order from Digikey. Existing LED matricies will be used for a personal project. Goal for now is to get LED matricies working, and then look into additional sensors/tech.

# Electronic Parts BOM:
Raspberry Pi 4, 8GB w/ GPIO Header - https://www.digikey.com/en/products/detail/raspberry-pi/SC0195-9/12159401?s=N4IgTCBcDaIMoGEAMBGAnAVgBRoJQgF0BfIA

Adafruit 64x32 RGB LED Matrix - https://www.digikey.com/en/products/detail/adafruit-industries-llc/5036/14671681?s=N4IgTCBcDaIIwFYwA4C0CAMBmAbKgdgCYgC6AvkA

Adafruit RGB LED Matrix Driver w/ RPi HAT - https://www.digikey.com/en/products/detail/adafruit-industries-llc/3211/8535237?s=N4IgTCBcDaIIwFYwA4C0YEIOyoHYBMQBdAXyA

40mm Fan (Research Required - need max CFM while keeping db low) - https://www.digikey.com/en/products/detail/sunon-fans/MF40101VX-1000U-A99/6198736

# Furbitten Studios BOM:
Starfighter Helmet - https://www.furbittenstudios.com/product-page/starfighter-protogen-helmet-kit

Bird Protogen Visor - https://www.furbittenstudios.com/product-page/bird-protogen-visor

Electronic Frames - https://www.furbittenstudios.com/product-page/electroncis-frames

TPU Ears - https://www.furbittenstudios.com/product-page/protogen-tpu-ears

Vent Fan Add-on - https://www.furbittenstudios.com/product-page/fan-vent-add-on

# Future steps will be added as we work on getting a baseline idea of how we want to approach this project.
