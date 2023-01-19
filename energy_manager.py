# !/usr/bin/env python
# -*- coding: utf-8 -*-

# Python 2/3 compatibility imports
from __future__ import print_function

# standard library imports
import json  # for working with data file
from threading import Thread
from time import sleep
import os

# local module imports
from blinker import signal
import gv  # Get access to SIP's settings
from sip import template_render  #  Needed for working with web.py templates
from urls import urls  # Get access to SIP's URLs
import web  # web.py framework
from webpages import ProtectedPage

try:
    from db_logger_core import db_logger_read_definitions
    from db_logger_generic_table import create_generic_table, add_date_generic_table
    withDBLogger = True
except ImportError:
    withDBLogger = False

# Add new URLs to access classes in this plugin.
# fmt: off
urls.extend([
    u"/energy-manager-set", u"plugins.energy_manager.settings",
    u"/energy-manager-set-save", u"plugins.energy_manager.save_settings",
    u"/energy-manager-home", u"plugins.energy_manager.home",
    ])
# fmt: on

# Add this plugin to the PLUGINS menu ["Menu Name", "URL"], (Optional)
gv.plugin_menu.append([_(u"Energy Plugin"), u"/energy-manager-set"])

settingsEnergyManager = {}

def energy_generate_default_array():
    defualtValue = {'timeInterValReg': 5, 'timeInterCharge': 15, 'netMeter': [], 'solarMeter': [], 'windMeter': [], 'otherSrcMeter': []}
    return defualtValue

# Read in the commands for this plugin from it's JSON file
def load_commands_energy():
    global settingsEnergyManager

    try:
        with open(u"./data/energy_manager.json", u"r") as f:  # Read settings from json file if it exists
            settingsEnergyManager = json.load(f)
    except IOError:  # If file does not exist return empty value
        settingsEnergyManager = energy_generate_default_array()
        # write default values to files
        with open(u"./data/energy_manager_tmp.json", u"w") as f:  # Edit: change name of json file
                json.dump(settingsEnergyManager, f)  # save to file
        with open(u"./data/energy_manager.json", u"w") as f:  # Edit: change name of json file
                json.dump(settingsEnergyManager, f)  # save to file

load_commands_energy()

class settings(ProtectedPage):
    """
    Load an html page for entering plugin settings.
    """
    def GET(self):
        if os.path.exists(u"./data/energy_manager_tmp.json"):
            file2Read = u"./data/energy_manager_tmp.json"
        else:
            file2Read = u"./data/energy_manager.json"
        try:
            with open(file2Read, u"r") as f:
                settingsEnergyManagerLocal = json.load(f)
        except IOError:  # If file does not exist return empty value
            settingsEnergyManagerLocal = {}  # TODO, fill with default data
        return template_render.energy_manager(settingsEnergyManagerLocal, withDBLogger)  # open settings page

class save_settings(ProtectedPage):
    def GET(self):
        qdict = web.input()

        needToReboot = False

        try:
            with open(u"./data/energy_manager_tmp.json", u"r") as f:
                settingsEnergyManagerLocal = json.load(f)
        except IOError:  # If file does not exist return empty value
            settingsEnergyManagerLocal = energy_generate_default_array()

        # TODO: combine tmp variable with final definitions

        if "timeInterValReg" in qdict:
            try:
                settingsEnergyManagerLocal["timeInterValReg"] = int(qdict["timeInterValReg"])
            except ValueError:
                print("That's not an int!")

        if "timeInterCharge" in qdict:
            try:
                settingsEnergyManagerLocal["timeInterCharge"] = int(qdict["timeInterCharge"])
            except ValueError:
                print("That's not an int!")

        # read number of sensor for net
        if "netMeter" in qdict:
            try:
                numberOfNetMeter = int(qdict["netMeter"])
                if "netMeter" not in settingsEnergyManagerLocal:
                    settingsEnergyManagerLocal["netMeter"] = [""] * numberOfNetMeter
                elif len(settingsEnergyManagerLocal["netMeter"]) < numberOfNetMeter:
                    increase = [""] * (numberOfNetMeter - len(settingsEnergyManagerLocal["netMeter"]))
                    settingsEnergyManagerLocal["netMeter"].extend(increase)
                else:
                    settingsEnergyManagerLocal["netMeter"] = settingsEnergyManagerLocal["netMeter"][:numberOfNetMeter]
            except ValueError:
                print("That's not an int!")

        # read number of solar meter
        if "solarMeter" in qdict:
            try:
                numberOfSolarMeter = int(qdict["solarMeter"])
                if "solarMeter" not in settingsEnergyManagerLocal:
                    settingsEnergyManagerLocal["solarMeter"] = [""] * numberOfSolarMeter
                elif len(settingsEnergyManagerLocal["solarMeter"]) < numberOfSolarMeter:
                    increase = [""] * (numberOfSolarMeter - len(settingsEnergyManagerLocal["solarMeter"]))
                    settingsEnergyManagerLocal["solarMeter"].extend(increase)
                else:
                    settingsEnergyManagerLocal["solarMeter"] = settingsEnergyManagerLocal["solarMeter"][:numberOfSolarMeter]
            except ValueError:
                print("That's not an int!")

        # read number of wind meter
        if "windMeter" in qdict:
            try:
                numberOfWindMeter = int(qdict["windMeter"])
                if "windMeter" not in settingsEnergyManagerLocal:
                    settingsEnergyManagerLocal["windMeter"] = [""] * numberOfWindMeter
                elif len(settingsEnergyManagerLocal["windMeter"]) < numberOfWindMeter:
                    increase = [""] * (numberOfWindMeter - len(settingsEnergyManagerLocal["windMeter"]))
                    settingsEnergyManagerLocal["windMeter"].extend(increase)
                else:
                    settingsEnergyManagerLocal["windMeter"] = settingsEnergyManagerLocal["windMeter"][:numberOfWindMeter]
            except ValueError:
                print("That's not an int!")

        if "otherSrcMeter" in qdict:
            try:
                numberOfOtherSrcMeter = int(qdict["otherSrcMeter"])
                if "otherSrcMeter" not in settingsEnergyManagerLocal:
                    settingsEnergyManagerLocal["otherSrcMeter"] = [""] * numberOfOtherSrcMeter
                elif len(settingsEnergyManagerLocal["otherSrcMeter"]) < numberOfOtherSrcMeter:
                    increase = [""] * (numberOfOtherSrcMeter - len(settingsEnergyManagerLocal["otherSrcMeter"]))
                    settingsEnergyManagerLocal["otherSrcMeter"].extend(increase)
                else:
                    settingsEnergyManagerLocal["otherSrcMeter"] = settingsEnergyManagerLocal["otherSrcMeter"][:numberOfOtherSrcMeter]
            except ValueError:
                print("That's not an int!")

        if 'netMeter' in settingsEnergyManagerLocal and len(settingsEnergyManagerLocal['netMeter']) > 0 and 'netMeter0' in qdict:
            needToReboot = True
            numberOfNetMeter = len(settingsEnergyManagerLocal['netMeter'])
            for i in range(numberOfNetMeter):
                if ('netMeter' + str(i)) in qdict and ('netMeterDevice' + str(i)) in qdict:
                    settingsEnergyManagerLocal['netMeter'][i] = [qdict['netMeter' + str(i)], qdict['netMeterDevice' + str(i)]]

        if 'solarMeter' in settingsEnergyManagerLocal and len(settingsEnergyManagerLocal['solarMeter']) > 0 and 'solarMeter0' in qdict:
            needToReboot = True
            numberOfSolarMeter = len(settingsEnergyManagerLocal['solarMeter'])
            for i in range(numberOfSolarMeter):
                if ('solarMeter' + str(i)) in qdict and ('solarMeter' + str(i)) in qdict:
                    settingsEnergyManagerLocal['solarMeter'][i] = [qdict['solarMeter' + str(i)], qdict['solarMeterDevice' + str(i)]]

        if 'windMeter' in settingsEnergyManagerLocal and len(settingsEnergyManagerLocal['windMeter']) > 0 and 'windMeter0' in qdict:
            needToReboot = True
            numberOfSolarMeter = len(settingsEnergyManagerLocal['windMeter'])
            for i in range(numberOfSolarMeter):
                if ('windMeter' + str(i)) in qdict and ('windMeter' + str(i)) in qdict:
                    settingsEnergyManagerLocal['windMeter'][i] = [qdict['windMeter' + str(i)], qdict['windMeterDevice' + str(i)]]

        if 'otherSrcMeter' in settingsEnergyManagerLocal and len(settingsEnergyManagerLocal['otherSrcMeter']) > 0 and 'otherSrcMeter0' in qdict:
            needToReboot = True
            numberOfSolarMeter = len(settingsEnergyManagerLocal['otherSrcMeter'])
            for i in range(numberOfSolarMeter):
                if ('otherSrcMeter' + str(i)) in qdict and ('otherSrcMeter' + str(i)) in qdict:
                    settingsEnergyManagerLocal['otherSrcMeter'][i] = [qdict['otherSrcMeter' + str(i)], qdict['otherSrcMeterDevice' + str(i)]]

        with open(u"./data/energy_manager_tmp.json", u"w") as f:  # Edit: change name of json file
            json.dump(settingsEnergyManagerLocal, f)  # save to file

        if needToReboot:
            with open(u"./data/energy_manager.json", u"w") as f:  # Edit: change name of json file
                json.dump(settingsEnergyManagerLocal, f)  # save to file
            raise web.seeother(u"/restart") # if same definitions change need to reboot
        else:
            raise web.seeother(u"/energy-manager-set")  # Return to definition pannel

class home(ProtectedPage):
    """
    Load an html page for entering plugin settings.
    """

    def GET(self):
        settings = {}
        return template_render.energy_manager_home(settings)  # open settings page


