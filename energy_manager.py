# !/usr/bin/env python
# -*- coding: utf-8 -*-

# Python 2/3 compatibility imports
from __future__ import print_function

# standard library imports
import json  # for working with data file
from threading import Thread, Lock
from time import sleep
import os
from datetime import datetime
from datetime import timedelta
import itertools
import copy

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

from energy_manager_aux import get_raw_reading_shelly_em3, get_raw_reading_shelly_em

# Add new URLs to access classes in this plugin.
# fmt: off
urls.extend([
    u"/energy-manager-set", u"plugins.energy_manager.settings",
    u"/energy-manager-set-save", u"plugins.energy_manager.save_settings",
    u"/energy-manager-home", u"plugins.energy_manager.home",
    u"/energy-manager-subscribe-consuption", u"plugins.energy_manager.energy_equipment",
    u"/energy-manager-ask-consuption", u"plugins.energy_manager.energy_resquest_permition",
    u"/energy-manager-price-definition", u"plugins.energy_manager.energy_price_definition",
    u"/energy-manager-price-definition-save", u"plugins.energy_manager.save_settings_energy_price",
    u"/energy-manager-price-definition-delete", u"plugins.energy_manager.delete_settings_energy_price"
    ])
# fmt: on

# Add this plugin to the PLUGINS menu ["Menu Name", "URL"], (Optional)
gv.plugin_menu.append([_(u"Energy Plugin"), u"/energy-manager-set"])
gv.plugin_menu.sort()
gv.plugin_menu = list(gv.plugin_menu for gv.plugin_menu,_ in itertools.groupby(gv.plugin_menu))

settingsEnergyManager = {}
isMainTreadRun = True

definitionPricesEnergy = {}
lockDefinitionPricesEnergy = Lock()

dataPrices = {} # save expectation prices in the future to start to undestand when is the best time to turn on equipment
mutexPrices = Lock()

listDeviceKnowConsp = {}
mutexDeviceKnowConsp = Lock()

listSubscriptionGetEnergy = {}
mutexSubscriptionGetEnergy = Lock()

def energy_generate_default_array():
    defualtValue = {'timeInterValReg': 5, 'timeInterCharge': 15, 'netMeter': [], 'solarMeter': [], 'windMeter': [], 'otherSrcMeter': []}
    return defualtValue

def mainThread(arg):
    global isMainTreadRun
    global settingsEnergyManager

    if 'timeInterValReg' in arg and 'timeInterCharge' in arg:
        timeIterVarReg = arg['timeInterValReg']
        timeInterCharge = arg['timeInterCharge']
    else:
        return

    dbDefinitions = {}
    if withDBLogger:
        dbDefinitions = db_logger_read_definitions()

        # create table with charge
        listElements = {"EnergyChargeDateTime" : "datetime", "EnergyChargeRecv" : "double", "EnergyChargeSend" : "double", "EnergyChargeRecvEffect" : "double", "EnergyChargeSendEffect" : "double"}
        create_generic_table("energy_charge", listElements, dbDefinitions)

        # create table with all register accum energy from all type of source
        listElements = {"EnergyPwrRegDateTime" : "datetime", "EnergyPwrRegMeterRecv" : "double", "EnergyPwrRegMeterSend" : "double", "EnergyPwrRegSolarSend": "double", "EnergyPwrRegWindSend": "double", "EnergyPwrRegOtherSend": "double", "EnergyPwrRegPWRMeter": "double", "EnergyPwrRegPWRSolar": "double", "EnergyPwrRegPWRWind": "double", "EnergyPwrRegPWROther": "double"}
        create_generic_table("energy_pwr_reg", listElements, dbDefinitions)

    isFirstRead = True

    # time to chage should be begger than regiter
    if timeIterVarReg > timeInterCharge:
        timeIterVarReg = timeInterCharge

    timeMinRead = min(timeIterVarReg, timeInterCharge)
    listMin2Read = []
    for i in range(0, 60, timeMinRead):
        listMin2Read.append(i)
    listMin2ReadReg = []
    for i in range(0, 60, timeIterVarReg):
        listMin2ReadReg.append(i)
    listMin2ReadCharge = []
    for i in range(0, 60, timeInterCharge):
        listMin2ReadCharge.append(i)

    lastTotalEnergytAccMeter = 0
    lastTotalEnergyAccGen = 0

    while isMainTreadRun:
        # every timeInterValReg get reading, read in hour sub intervals
        nowDateTime = datetime.now()
        nowDateTimeMinute = nowDateTime.minute
        nextMinute = 0
        for nextMin in listMin2Read:
            if nowDateTimeMinute < nextMin:
                nextMinute = nextMin
                break

        is2Register = (nextMinute in listMin2ReadReg)
        is2Charge = (nextMinute in listMin2ReadCharge)

        if nextMinute == 0:
            nextCycle = datetime(nowDateTime.year, nowDateTime.month, nowDateTime.day, nowDateTime.hour, 0, 0) + timedelta(hours=1)
        else:
            nextCycle = datetime(nowDateTime.year, nowDateTime.month, nowDateTime.day, nowDateTime.hour, nextMinute, 0)

        delta = nextCycle - nowDateTime
        sleep(delta.total_seconds())

        # read all sensors data, avoid read same sensor but in diferent channels
        repeatedReading = {}

        # Net meter
        totalPowerMeter = 0.0
        totalEnergytAccMeter = 0.0
        totalEnergyAccGen = 0.0

        netMetterReading = []
        for currMeter in arg['netMeter']:
            if len(currMeter) == 2:
                if currMeter[1] == 'shellyEM3':
                    if currMeter[0] in repeatedReading:
                        newReading = repeatedReading[currMeter[0]]
                        netMetterReading.append(newReading)
                    else:
                        newReading = get_raw_reading_shelly_em3(currMeter[0])
                        # TODO: Check when fail reading
                        netMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading
                    totalPowerMeter = totalPowerMeter + sum(newReading['power'])
                    totalEnergytAccMeter = totalEnergytAccMeter + sum(newReading['accCons'])
                    totalEnergyAccGen = totalEnergyAccGen + sum(newReading['accSend'])
                elif currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_1' or currMeter[1] == 'shellyEM3_2' or currMeter[1] == 'shellyEM3_3':
                    if currMeter[0] in repeatedReading:
                        netMetterReading.append(repeatedReading[currMeter[0]])
                    else:
                        newReading = get_raw_reading_shelly_em(currMeter[0])
                        # TODO: Check when fail reading
                        netMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading

                    idxClip = -1
                    if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                        idxClip = 0
                    elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                        idxClip = 1
                    elif currMeter[1] == 'shellyEM3_3':
                        idxClip = 2
                    if idxClip >= 0:
                        totalPowerMeter = totalPowerMeter + newReading['power'][idxClip]
                        totalEnergytAccMeter = totalEnergytAccMeter + newReading['accCons'][idxClip]
                        totalEnergyAccGen = totalEnergyAccGen + newReading['accSend'][idxClip]

        # Solar meter
        totalPowerSolar = 0.0
        totalEnergySolar = 0.0
        solarMetterReading = []

        for currMeter in arg['solarMeter']:
            if len(currMeter) == 2:
                if currMeter[1] == 'shellyEM3':
                    if currMeter[0] in repeatedReading:
                        solarMetterReading.append(repeatedReading[currMeter[0]])
                    else:
                        newReading = get_raw_reading_shelly_em3(currMeter[0])
                        # TODO: Check when fail reading
                        solarMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading
                    totalPowerGenerate = totalPowerGenerate + sum(newReading['power'])
                    totalPowerSolar = totalPowerSolar + sum(newReading['power'])
                    totalEnergySolar = totalEnergySolar + max(sum(newReading['accCons']), sum(newReading['accSend'])) # can be mounted any direction for generate
                elif currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_1' or currMeter[1] == 'shellyEM3_2' or currMeter[1] == 'shellyEM3_3':
                    if currMeter[0] in repeatedReading:
                        newReading = repeatedReading[currMeter[0]]
                        solarMetterReading.append(newReading)
                    else:
                        newReading = get_raw_reading_shelly_em(currMeter[0])
                        solarMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading

                    idxClip = -1
                    if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                        idxClip = 0
                    elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                        idxClip = 1
                    elif currMeter[1] == 'shellyEM3_3':
                        idxClip = 2
                    if idxClip >= 0:
                        totalPowerGenerate = totalPowerGenerate + newReading['power'][idxClip]
                        totalPowerSolar = totalPowerSolar + newReading['power'][idxClip]
                        totalEnergySolar = totalEnergySolar + max(newReading['accCons'][idxClip], newReading['accSend'][idxClip])

        # Wind meter
        totalPowerWind = 0.0
        totalEnergyWind = 0.0
        windMetterReading = []

        for currMeter in arg['solarMeter']:
            if len(currMeter) == 2:
                if currMeter[1] == 'shellyEM3':
                    if currMeter[0] in repeatedReading:
                        newReading = repeatedReading[currMeter[0]]
                        windMetterReading.append(newReading)
                    else:
                        newReading = get_raw_reading_shelly_em3(currMeter[0])
                        # TODO: Check when fail reading
                        windMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading
                    totalPowerGenerate = totalPowerGenerate + sum(newReading['power'])
                    totalPowerWind = totalPowerWind + sum(newReading['power'])
                    totalEnergyWind = totalEnergyWind + max(sum(newReading['accCons']), sum(newReading['accSend'])) # can be mounted any direction for generate
                elif currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_1' or currMeter[1] == 'shellyEM3_2' or currMeter[1] == 'shellyEM3_3':
                    if currMeter[0] in repeatedReading:
                        windMetterReading.append(repeatedReading[currMeter[0]])
                    else:
                        newReading = get_raw_reading_shelly_em(currMeter[0])
                        # TODO: Check when fail reading
                        windMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading

                    idxClip = -1
                    if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                        idxClip = 0
                    elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                        idxClip = 1
                    elif currMeter[1] == 'shellyEM3_3':
                        idxClip = 2
                    if idxClip >= 0:
                        totalPowerGenerate = totalPowerGenerate + newReading['power'][idxClip]
                        totalPowerWind = totalPowerWind + newReading['power'][idxClip]
                        totalEnergyWind = totalEnergyWind + max(newReading['accCons'][idxClip], newReading['accSend'][idxClip])

        # Other meter
        totalPowerOther = 0
        totalEnergyOther = 0
        otherMetterReading = []

        for currMeter in arg['otherSrcMeter']:
            if len(currMeter) == 2:
                if currMeter[1] == 'shellyEM3':
                    if currMeter[0] in repeatedReading:
                        newReading = repeatedReading[currMeter[0]]
                        otherMetterReading.append(newReading)
                    else:
                        newReading = get_raw_reading_shelly_em3(currMeter[0])
                        # TODO: Check when fail reading
                        otherMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading
                    totalPowerGenerate = totalPowerGenerate + sum(newReading['power'])
                    totalPowerOther = totalPowerOther + sum(newReading['power'])
                    totalEnergyOther = totalEnergyOther + max(sum(newReading['accCons']), sum(newReading['accSend'])) # can be mounted any direction for generate
                elif currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_1' or currMeter[1] == 'shellyEM3_2' or currMeter[1] == 'shellyEM3_3':
                    if currMeter[0] in repeatedReading:
                        otherMetterReading.append(repeatedReading[currMeter[0]])
                    else:
                        newReading = get_raw_reading_shelly_em(currMeter[0])
                        # TODO: Check when fail reading
                        otherMetterReading.append(newReading)
                        repeatedReading[currMeter[0]] = newReading

                    idxClip = -1
                    if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                        idxClip = 0
                    elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                        idxClip = 1
                    elif currMeter[1] == 'shellyEM3_3':
                        idxClip = 2
                    if idxClip >= 0:
                        totalPowerGenerate = totalPowerGenerate + newReading['power'][idxClip]
                        totalPowerOther = totalPowerOther + newReading['power'][idxClip]
                        totalEnergyOther = totalEnergyOther + max(newReading['accCons'][idxClip], newReading['accSend'][idxClip])

        # Total power meter should be negative if have excedent of energy
        # totalPowerConsuption = totalPowerGenerate + totalPowerMeter

        curentDate = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # write 2 DB
        if is2Register:
            # save generic generation
            listData = [curentDate, totalEnergytAccMeter, totalEnergyAccGen, totalEnergySolar, totalEnergyWind, totalEnergyOther, totalPowerMeter, totalPowerSolar, totalPowerWind, totalPowerOther]
            add_date_generic_table('energy_pwr_reg', listData, dbDefinitions)

            # save raw data from all sensors
            # NET, solar, wind and ohet meter sensors
            listMetersName = ['netMeter', 'solarMeter', 'windMeter', 'otherSrcMeter']
            for meterName in listMetersName:
                for i in range(len(arg[meterName])):
                    listData = []
                    listData.append(curentDate)
                    if arg[meterName][i][1] == 'shellyEM3':
                        listElements = {"EnergyManagerTime" : "datetime", "EnergyManagerPower0" : "double", "EnergyManagerPF0" : "double", "EnergyManagerCurrent0" : "double", "EnergyManagerVoltage0" : "double", "EnergyManagerTotal0" : "double", "EnergyManagerTotalRet0" : "double", "EnergyManagerPower1" : "double", "EnergyManagerPF1" : "double", "EnergyManagerCurrent1" : "double", "EnergyManagerVoltage1" : "double", "EnergyManagerTotal1" : "double", "EnergyManagerTotalRet1" : "double", "EnergyManagerPower2" : "double", "EnergyManagerPF2" : "double", "EnergyManagerCurrent2" : "double", "EnergyManagerVoltage2" : "double", "EnergyManagerTotal2" : "double", "EnergyManagerTotalRet2" : "double"}
                        for k in range(3):
                            if meterName == 'netMeter':
                                listData.extend([netMetterReading[i]['power'][k], netMetterReading[i]['pf'][k], netMetterReading[i]['current'][k], netMetterReading[i]['voltage'][k], netMetterReading[i]['accCons'][k], netMetterReading[i]['accSend'][k]])
                            elif meterName == 'solarMeter':
                                listData.extend([solarMetterReading[i]['power'][k], solarMetterReading[i]['pf'][k], solarMetterReading[i]['current'][k], solarMetterReading[i]['voltage'][k], solarMetterReading[i]['accCons'][k], solarMetterReading[i]['accSend'][k]])
                            elif meterName == 'windMeter':
                                listData.extend([windMetterReading[i]['power'][k], windMetterReading[i]['pf'][k], windMetterReading[i]['current'][k], windMetterReading[i]['voltage'][k], windMetterReading[i]['accCons'][k], windMetterReading[i]['accSend'][k]])
                            elif meterName == 'otherSrcMeter':
                                listData.extend([otherMetterReading[i]['power'][k], otherMetterReading[i]['pf'][k], otherMetterReading[i]['current'][k], otherMetterReading[i]['voltage'][k], otherMetterReading[i]['accCons'][k], otherMetterReading[i]['accSend'][k]])
                    else:
                        listElements = {"EnergyManagerTime" : "datetime", "EnergyManagerPower0" : "double", "EnergyManagerPF0" : "double", "EnergyManagerCurrent0" : "double", "EnergyManagerVoltage0" : "double", "EnergyManagerIsValid0" : "boolean", "EnergyManagerTotal0" : "double", "EnergyManagerTotalRet0" : "double"}
                        idxClip = -1
                        if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                            idxClip = 0
                        elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                            idxClip = 1
                        elif currMeter[1] == 'shellyEM3_3':
                            idxClip = 2
                        if idxClip >= 0:
                            if meterName == 'netMeter':
                                listData.extend([netMetterReading[i]['power'][idxClip], netMetterReading[i]['pf'][idxClip], netMetterReading[i]['current'][idxClip], netMetterReading[i]['voltage'][idxClip], netMetterReading[i]['accCons'][idxClip], netMetterReading[i]['accSend'][idxClip]])
                            elif meterName == 'solarMeter':
                                listData.extend([solarMetterReading[i]['power'][idxClip], solarMetterReading[i]['pf'][idxClip], solarMetterReading[i]['current'][idxClip], solarMetterReading[i]['voltage'][idxClip], solarMetterReading[i]['accCons'][idxClip], solarMetterReading[i]['accSend'][idxClip]])
                            elif meterName == 'windMeter':
                                listData.extend([windMetterReading[i]['power'][idxClip], windMetterReading[i]['pf'][idxClip], windMetterReading[i]['current'][idxClip], windMetterReading[i]['voltage'][idxClip], windMetterReading[i]['accCons'][idxClip], windMetterReading[i]['accSend'][idxClip]])
                            elif meterName == 'otherSrcMeter':
                                listData.extend([otherMetterReading[i]['power'][idxClip], otherMetterReading[i]['pf'][idxClip], otherMetterReading[i]['current'][idxClip], otherMetterReading[i]['voltage'][idxClip], otherMetterReading[i]['accCons'][idxClip], otherMetterReading[i]['accSend'][idxClip]])


                    tableName = ''
                    if meterName == 'netMeter':
                        tableName = "energy_manager_raw_meter_"+ str(i)
                    elif meterName == 'solarMeter':
                        tableName = "energy_manager_raw_solar_"+ str(i)
                    elif meterName == 'windMeter':
                        tableName = "energy_manager_raw_wind_"+ str(i)
                    elif meterName == 'otherSrcMeter':
                        tableName = "energy_manager_raw_other_"+ str(i)

                    if len(tableName) > 0:
                        create_generic_table(tableName, listElements, dbDefinitions)
                        add_date_generic_table(tableName, listData, dbDefinitions)

        if is2Charge:
            if not isFirstRead:
                diffEnergyCons = totalEnergytAccMeter - lastTotalEnergytAccMeter
                diffEnergySend = totalEnergyAccGen - lastTotalEnergyAccGen

                # save 2 charge table
                if timeInterCharge > 0:
                    # need to discount consuption
                    diffEnergyConsEffective = diffEnergyCons - diffEnergySend
                    if diffEnergyConsEffective < 0:
                        diffEnergySendEffective = abs(diffEnergyConsEffective)
                        diffEnergyConsEffective = 0
                    else:
                        diffEnergySendEffective = 0
                else:
                    diffEnergyConsEffective = diffEnergyCons
                    diffEnergySendEffective = diffEnergySend

                # save consuption 2 db is exits
                if withDBLogger:
                    listData = [curentDate, diffEnergyCons, diffEnergySend, diffEnergyConsEffective, diffEnergySendEffective]
                    add_date_generic_table('energy_charge', listData, dbDefinitions)
            else:
                isFirstRead = False

            lastTotalEnergytAccMeter = totalEnergytAccMeter
            lastTotalEnergyAccGen = totalEnergyAccGen

def updatePriceAndAvailabilityEnergy(arg):
    global isMainTreadRun
    while isMainTreadRun:
        sleep(5)

def checkDevicesWaitingForEnergy():
    global isMainTreadRun
    while isMainTreadRun:
        sleep(5)

# Read in the commands for this plugin from it's JSON file
def load_commands_energy():
    global settingsEnergyManager
    global isMainTreadRun, threadMain, threadPrices

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

    # Launch threads
    isMainTreadRun = True

    threadMain = Thread(target = mainThread, args = (settingsEnergyManager,))
    threadMain.start()

    threadPrices = Thread(target = updatePriceAndAvailabilityEnergy, args = (settingsEnergyManager,))
    threadPrices.start()

def stopMainTread():
    global isMainTreadRun, threadMain, threadPrices
    isMainTreadRun = False

    threadMain.join()
    threadPrices.join()

delay_expired = signal("restarting")
delay_expired.connect(stopMainTread) 

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

class energy_equipment(ProtectedPage):
    """
    inform energy manager device is workig
    """

    def GET(self):
        qdict = web.input()

        if "ExtentionName" in qdict and "DeviceRef" in qdict and "NewState" in qdict and "PowerDevice" in qdict and (qdict["NewState"] == 'on' or qdict["NewState"] == 'off'):
            extentionName = qdict["ExtentionName"]
            deviceRef = qdict["DeviceRef"]
            newState = qdict["NewState"] == 'on'
            powerDevice = qdict["PowerDevice"]

            currentKey = extentionName +":"+ deviceRef

            mutexDeviceKnowConsp.acquire()
            listDeviceKnowConsp[currentKey] = {}
            listDeviceKnowConsp[currentKey]["NewState"] = newState
            listDeviceKnowConsp[currentKey]["PowerDevice"] = powerDevice
            mutexDeviceKnowConsp.release()

class energy_resquest_permition(ProtectedPage):
    """
    add new subscription to energy, client is waiting to use non priority energy
    """

    def GET(self):
        qdict = web.input()

        if "ExtentionName" in qdict and "DeviceRef" in qdict and "LinkConn" in qdict and "MinWorkingTime" and "ExpectedWorkingTime" in qdict and "EnergyPower" in qdict:
            ententionName = qdict["ExtentionName"]
            deviceRef = qdict["DeviceRef"]
            linkConn = qdict["LinkConn"]
            try:
                minWorkingTime = float(qdict["MinWorkingTime"])
                energyPower = float(qdict["EnergyPower"])
                expectedWorkingTime = float(qdict["ExpectedWorkingTime"])
            except:
                return "NOK"

            avoidIrrigationProgram = False
            if "AvoidIrrigationProgram" in qdict and qdict["AvoidIrrigationProgram"] == 'yes':
                avoidIrrigationProgram = True

            hoursCanWait = 0 # 0 means infinite
            if "HoursCanWait" in qdict:
                try:
                    # try converting to integer
                    hoursCanWait = int(qdict["HoursCanWait"])
                except ValueError:
                    hoursCanWait = 0
            mutexSubscriptionGetEnergy.acquire()
            currentKey = ententionName + ":" + deviceRef

            listSubscriptionGetEnergy[currentKey] = {}
            listSubscriptionGetEnergy[currentKey]["ExtentionName"] = ententionName
            listSubscriptionGetEnergy[currentKey]["DeviceRef"] = deviceRef
            listSubscriptionGetEnergy[currentKey]["LinkConn"] = linkConn
            listSubscriptionGetEnergy[currentKey]["MinWorkingTime"] = minWorkingTime
            listSubscriptionGetEnergy[currentKey]["EnergyPower"] = energyPower
            listSubscriptionGetEnergy[currentKey]["ExpectedWorkingTime"] = expectedWorkingTime
            listSubscriptionGetEnergy[currentKey]["AvoidIrrigationProgram"] = avoidIrrigationProgram
            listSubscriptionGetEnergy[currentKey]["HoursCanWait"] = hoursCanWait
            mutexSubscriptionGetEnergy.release()

            return "WAIT"
            #retrun "OK" # energy is available, device can run now

        # inform error
        return "NOK"

class energy_price_definition(ProtectedPage):
    """
    GUI to define prices
    """
    def GET(self):
        global definitionPricesEnergy, lockDefinitionPricesEnergy

        qdict = web.input()

        editEntry = -1
        if "editValue" in qdict:
            try:
                editEntry = int(qdict["editValue"])
            except:
                pass

        lockDefinitionPricesEnergy.acquire()
        try:
            with open(u"./data/energy_manager_prices.json", u"r") as f:  # Read settings from json file if it exists
                definitionPricesEnergy = json.load(f)
        except IOError:
            definitionPricesEnergy = {"energyDefaultPrice": 0.15, "energyEntryPrice": []}

        definitionPricesEnergyTMP = copy.deepcopy(definitionPricesEnergy)
        lockDefinitionPricesEnergy.release()

        return template_render.energy_manager_price_table(definitionPricesEnergyTMP, editEntry)

class save_settings_energy_price(ProtectedPage):
    def GET(self):
        global definitionPricesEnergy, lockDefinitionPricesEnergy

        qdict = web.input()

        lockDefinitionPricesEnergy.acquire()
        defionionPricesEnergyTmp = copy.deepcopy(definitionPricesEnergy)
        lockDefinitionPricesEnergy.release()

        if "energyDefaultPrice" not in defionionPricesEnergyTmp:
            defionionPricesEnergyTmp["energyDefaultPrice"] = 0.15

        if "energyDefaultPrice" in qdict:
            try:
                newEnergyPrice = float(qdict["energyDefaultPrice"])
                if newEnergyPrice > 0:
                    defionionPricesEnergyTmp["energyDefaultPrice"] = newEnergyPrice
            except:
                pass

        if "energyCurrentPrice" in qdict:
            try:
                newEnergyPriceCurrent = float(qdict["energyCurrentPrice"])
                if newEnergyPriceCurrent > 0 and "energyTimeInit" in qdict and "energyTimeEnd" in qdict and \
                   "energyValidDateInit" in qdict and "energyValidDateEnd" in qdict:
                    if "energyEntryPrice" not in defionionPricesEnergyTmp:
                        defionionPricesEnergyTmp["energyEntryPrice"] = []

                    priceNewEntry = {'currentPrice': newEnergyPriceCurrent}
                    priceNewEntry.update({'minHour': qdict["energyTimeInit"], 'maxHour': qdict["energyTimeEnd"]})
                    priceNewEntry.update({'minDate': qdict["energyValidDateInit"], 'maxDate': qdict["energyValidDateEnd"]})

                    # check week days active
                    priceNewEntry.update({'monday': "monday" in qdict})
                    priceNewEntry.update({'tuesday': "tuesday" in qdict})
                    priceNewEntry.update({'wednesday': "wednesday" in qdict})
                    priceNewEntry.update({'thursday': "thursday" in qdict})
                    priceNewEntry.update({'friday': "friday" in qdict})
                    priceNewEntry.update({'saturday': "saturday" in qdict})
                    priceNewEntry.update({'sunday': "sunday" in qdict})

                    priceNewEntry.update({'idx': len(defionionPricesEnergyTmp["energyEntryPrice"])})

                    if "energyIdxEdit" in qdict:
                        if qdict["energyIdxEdit"].isdigit() and int(qdict["energyIdxEdit"]) >= 0 and int(qdict["energyIdxEdit"]) < len(defionionPricesEnergyTmp["energyEntryPrice"]):
                            defionionPricesEnergyTmp["energyEntryPrice"][int(qdict["energyIdxEdit"])] = priceNewEntry
                    else:
                        defionionPricesEnergyTmp["energyEntryPrice"].append(priceNewEntry)
            except:
                pass

        # sort entries by initial date
        list2Sort = []
        for element in defionionPricesEnergyTmp["energyEntryPrice"]:
            list2Sort.append(element['minDate'] + element['minHour'])
        idxSort = [i[0] for i in sorted(enumerate(list2Sort), key=lambda x:x[1])]

        sortList = []
        i = 0
        for currIdx in idxSort:
            defionionPricesEnergyTmp["energyEntryPrice"][currIdx]['idx'] = i
            sortList.append(defionionPricesEnergyTmp["energyEntryPrice"][currIdx])
            i = i + 1

        defionionPricesEnergyTmp["energyEntryPrice"] = sortList

        lockDefinitionPricesEnergy.acquire()
        definitionPricesEnergy = copy.deepcopy(defionionPricesEnergyTmp)
        with open(u"./data/energy_manager_prices.json", u"w") as f:  # Edit: change name of json file
                json.dump(definitionPricesEnergy, f)  # save to file
        lockDefinitionPricesEnergy.release()

        raise web.seeother(u"/energy-manager-price-definition")

class delete_settings_energy_price(ProtectedPage):
    def GET(self):
        global definitionPricesEnergy, lockDefinitionPricesEnergy

        qdict = web.input()
        if "deleteIdx" in qdict and qdict["deleteIdx"].isdigit():
            indxDelete = int(qdict["deleteIdx"])
            lockDefinitionPricesEnergy.acquire()
            if indxDelete >= 0 and indxDelete < len(definitionPricesEnergy["energyEntryPrice"]):
                del definitionPricesEnergy["energyEntryPrice"][indxDelete]
                for i in range(len(definitionPricesEnergy["energyEntryPrice"])):
                    definitionPricesEnergy["energyEntryPrice"][i]['idx'] = i

                with open(u"./data/energy_manager_prices.json", u"w") as f:  # Edit: change name of json file
                    json.dump(definitionPricesEnergy, f)  # save to file
            lockDefinitionPricesEnergy.release()

        raise web.seeother(u"/energy-manager-price-definition")


