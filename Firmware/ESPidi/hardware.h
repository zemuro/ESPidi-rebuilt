#ifndef HARDWARE_H
#define HARDWARE_H

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <MIDI.h>
#include "config.h"

extern Adafruit_SSD1306 display;
#ifdef ESPIDI_TEST
extern MIDI_NAMESPACE::MidiInterface<MIDI_NAMESPACE::SerialMIDI<ThSerial>> MIDI;
#else
extern MIDI_NAMESPACE::MidiInterface<MIDI_NAMESPACE::SerialMIDI<HardwareSerial>> MIDI;
#endif

void hw_initDisplay();
void hw_initMIDI();
void hw_initPins();

#endif