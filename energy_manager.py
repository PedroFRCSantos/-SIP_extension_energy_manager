# !/usr/bin/env python
# -*- coding: utf-8 -*-

# Python 2/3 compatibility imports
from __future__ import print_function
from http.client import LineTooLong

# standard library imports
import json  # for working with data file
from threading import Thread, Lock
from time import sleep
import os
from datetime import datetime
from datetime import timedelta
import itertools
import copy
from datetime import datetime,timezone
import math
import queue

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
    from db_logger_off_grid import add_db_values, get_init_value
    withDBLogger = True
except ImportError:
    withDBLogger = False

from energy_manager_aux import get_raw_reading_shelly_em3, get_raw_reading_shelly_em

# Add new URLs to access classes in this plugin.
# fmt: off
urls.extend([
    u"/energy-manager-set", u"plugins.energy_manager.settings",
    u"/energy-manager-set-save", u"plugins.energy_manager.save_settings",
    u"/off-grid-location", u"plugins.energy_manager.off_grid_location",
    u"/energy-manager-offgrid-set-save", u"plugins.energy_manager.off_grid_save_sett",
    u"/energy-manager-home", u"plugins.energy_manager.home",
    u"/energy-manager-subscribe-consuption", u"plugins.energy_manager.energy_equipment",
    u"/energy-manager-ask-consuption", u"plugins.energy_manager.energy_resquest_permition",
    u"/energy-manager-price-definition", u"plugins.energy_manager.energy_price_definition",
    u"/energy-manager-price-definition-save", u"plugins.energy_manager.save_settings_energy_price",
    u"/energy-manager-price-definition-delete", u"plugins.energy_manager.delete_settings_energy_price",
    u"/energy-manager-offgrid-init", u"plugins.energy_manager.offgrid_initial_data",
    u"/energy-manager-offgrid", u"plugins.energy_manager.offgrid_sensor",
    u"/energy-manager-offgrid-day-night", u"plugins.energy_manager.offgrid_day_night",
    u"/energy-manager-offgrid-demand", u"plugins.energy_manager.offgrid_ged_current_val",
    ])
# fmt: on

# Add this plugin to the PLUGINS menu ["Menu Name", "URL"], (Optional)
gv.plugin_menu.append([_(u"Energy Plugin"), u"/energy-manager-set"])
gv.plugin_menu.sort()
gv.plugin_menu = list(gv.plugin_menu for gv.plugin_menu,_ in itertools.groupby(gv.plugin_menu))

settingsEnergyManager = {}
isMainTreadRun = True

# Definition off-frid sensor
offGridStationsDef = {}
lockOffGridStationsDef = Lock()

# read data from http requests
commandsOffGridQueu = queue.Queue()
offGridDateOnDemand = {}
offGridDateOnDemandLock = Lock()
threadOfGridProcessData = None
threadOfGridProcessIsRunning = True


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

def processOffGridData():
    global threadOfGridProcessIsRunning, commandsOffGridQueu, offGridStationsDef, lockOffGridStationsDef

    lastDateTimeSave = None

    if withDBLogger:
        dbDefinitions = db_logger_read_definitions()

    while threadOfGridProcessIsRunning:
        dataOffGrind = commandsOffGridQueu.get()

        lockOffGridStationsDef.acquire()
        tmpDataOffGrid = copy.deepcopy(offGridStationsDef)
        lockOffGridStationsDef.release()

        if "OffGridRef" in dataOffGrind and dataOffGrind["OffGridRef"] in tmpDataOffGrid:
            # check if all data present
            allData = True
            for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["SolarN"]):
                if "VSOLAR" + str(i + 1) not in dataOffGrind or "CSOLAR" + str(i + 1) not in dataOffGrind or "ESOLAR" + str(i + 1) not in dataOffGrind:
                    allData = False
                else:
                    try:
                        dumpFloat = float(dataOffGrind["VSOLAR" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["CSOLAR" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["ESOLAR" + str(i + 1)])
                    except:
                        allData = False

            for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["WindN"]):
                if "VWIND" + str(i + 1) not in dataOffGrind or "CWIND" + str(i + 1) not in dataOffGrind or "EWIND" + str(i + 1) not in dataOffGrind:
                    allData = False
                else:
                    try:
                        dumpFloat = float(dataOffGrind["VWIND" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["CWIND" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["EWIND" + str(i + 1)])
                    except:
                        allData = False

            for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["TotalGen"]):
                if "VGENTOTAL" + str(i + 1) not in dataOffGrind or "CGENTOTAL" + str(i + 1) not in dataOffGrind or "EGENTOTAL" + str(i + 1) not in dataOffGrind:
                    allData = False
                else:
                    try:
                        dumpFloat = float(dataOffGrind["VGENTOTAL" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["CGENTOTAL" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["EGENTOTAL" + str(i + 1)])
                    except:
                        allData = False

            for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["TotalConspN"]):
                if "VCONSP" + str(i + 1) not in dataOffGrind or "CCONSP" + str(i + 1) not in dataOffGrind or "ECONSP" + str(i + 1) not in dataOffGrind:
                    allData = False
                else:
                    try:
                        dumpFloat = float(dataOffGrind["VCONSP" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["CCONSP" + str(i + 1)])
                        dumpFloat = float(dataOffGrind["ECONSP" + str(i + 1)])
                    except:
                        allData = False

            if "DateTime" not in dataOffGrind:
                allData = False

            if allData:
                # save to DB id needed
                offGridDateOnDemandLock.acquire()
                if dataOffGrind["OffGridRef"] not in offGridDateOnDemand:
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]] = {}

                for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["SolarN"]):
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VSOLAR"+ str(i + 1)] = float(dataOffGrind["VSOLAR" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CSOLAR"+ str(i + 1)] = float(dataOffGrind["CSOLAR" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["PSOLAR"+ str(i + 1)] = offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VSOLAR"+ str(i + 1)] * offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CSOLAR"+ str(i + 1)]
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["ESOLAR"+ str(i + 1)] = float(dataOffGrind["ESOLAR" + str(i + 1)])

                for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["WindN"]):
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VWIND"+ str(i + 1)] = float(dataOffGrind["VWIND" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CWIND"+ str(i + 1)] = float(dataOffGrind["CWIND" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["PWIND"+ str(i + 1)] = offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VWIND"+ str(i + 1)] * offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CWIND"+ str(i + 1)]
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["EWIND"+ str(i + 1)] = float(dataOffGrind["EWIND" + str(i + 1)])

                for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["TotalGen"]):
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VGENTOTAL"+ str(i + 1)] = float(dataOffGrind["VGENTOTAL" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CGENTOTAL"+ str(i + 1)] = float(dataOffGrind["CGENTOTAL" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["PGENTOTAL"+ str(i + 1)] = offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VGENTOTAL"+ str(i + 1)] * offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CGENTOTAL"+ str(i + 1)]
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["EGENTOTAL"+ str(i + 1)] = float(dataOffGrind["EGENTOTAL" + str(i + 1)])

                for i in range(tmpDataOffGrid[dataOffGrind["OffGridRef"]]["TotalConspN"]):
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VCONSP"+ str(i + 1)] = float(dataOffGrind["VCONSP" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CCONSP"+ str(i + 1)] = float(dataOffGrind["CCONSP" + str(i + 1)])
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["PCONSP"+ str(i + 1)] = offGridDateOnDemand[dataOffGrind["OffGridRef"]]["VCONSP"+ str(i + 1)] * offGridDateOnDemand[dataOffGrind["OffGridRef"]]["CCONSP"+ str(i + 1)]
                    offGridDateOnDemand[dataOffGrind["OffGridRef"]]["ECONSP"+ str(i + 1)] = float(dataOffGrind["ECONSP" + str(i + 1)])

                offGridDateOnDemand[dataOffGrind["OffGridRef"]]["DateTime"] = dataOffGrind["DateTime"]

                offGridDateOnDemandLock.release()

                if withDBLogger and (lastDateTimeSave == None or (dataOffGrind["DateTime"] - lastDateTimeSave).total_seconds() / 60.0 > 5.0):
                    add_db_values(dbDefinitions, tmpDataOffGrid, dataOffGrind)

                    lastDateTimeSave = dataOffGrind["DateTime"]

# Read in the commands for this plugin from it's JSON file
def load_commands_energy():
    global settingsEnergyManager, offGridStationsDef
    global isMainTreadRun, threadMain, threadPrices, threadOfGridProcessData

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

    lockOffGridStationsDef.acquire()
    try:
        with open(u"./data/energy_manager_offgrid.json", u"r") as f:  # Read settings from json file if it exists
            offGridStationsDef = json.load(f)
    except IOError:  # If file does not exist return empty value
        offGridStationsDef = {}
    lockOffGridStationsDef.release()

    # Launch threads
    isMainTreadRun = True

    threadMain = Thread(target = mainThread, args = (settingsEnergyManager,))
    threadMain.start()

    threadPrices = Thread(target = updatePriceAndAvailabilityEnergy, args = (settingsEnergyManager,))
    threadPrices.start()

    threadOfGridProcessData = Thread(target = processOffGridData)
    threadOfGridProcessData.start()

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

class off_grid_location(ProtectedPage):
    """
    Load an html page off-grid settings
    """

    def GET(self):
        global offGridStationsDef, lockOffGridStationsDef

        addNew = 0

        qdict = web.input()
        if "AddNew" in qdict:
            addNew = 1

        lockOffGridStationsDef.acquire()
        tmpData = copy.deepcopy(offGridStationsDef)
        lockOffGridStationsDef.release()

        return template_render.energy_manager_off_grid_def(tmpData, addNew)

class off_grid_save_sett(ProtectedPage):
    """
    Load save off-grid settings
    """

    def GET(self):
        global offGridStationsDef, lockOffGridStationsDef

        qdict = web.input()

        lockOffGridStationsDef.acquire()
        offGridStationsDef = {}

        # key in form name of station
        listOfFormStations = []
        for key in qdict:
            if len(key) > len("offGridStation") and key[:len("offGridStation")] == "offGridStation":
                listOfFormStations.append(key[len("offGridStation"):].replace(" ", ""))

        # save data to off-grid definitions
        for stationKey in listOfFormStations:
            offGridStationsDef[stationKey] = {'Lat': float(qdict["offgridlat" + stationKey]), 'Log': float(qdict["offgridlog" + stationKey]), 'SolarN': int(qdict["offgridsolar" + stationKey]), 'SolarVN': int(qdict["offgridvirtualsolar" + stationKey]), 'WindN': int(qdict["offgridwind" + stationKey]), 'WindVN': int(qdict["offgridvirtualwind" + stationKey]), 'TotalGen': int(qdict["offgridtotal" + stationKey]), 'TotalConspN': int(qdict["offgridconsumption" + stationKey]), 'SolarVNGenTotalId': [], 'SolarVNGenSolarId': [], 'SolarVNGenWindId': [], 'WindVNGenTotalId': [], 'WindVNGenSolarId': [], 'WindVNGenWindId': []}

        # add new station
        if "offGridStation" in qdict and "offgridlat" in qdict and "offgridsolar" in qdict and "offgridvirtualsolar" in qdict and "offgridwind" in qdict and "offgridvirtualwind" in qdict and "offgridtotal" in qdict and "offgridconsumption" in qdict:
            # create new station
            offGridStationsDef[qdict["offGridStation"].replace(" ", "")] = {'Lat': float(qdict["offgridlat"]), 'Log': float(qdict["offgridlog"]), 'SolarN': int(qdict["offgridsolar"]), 'SolarVN': int(qdict["offgridvirtualsolar"]), 'WindN': int(qdict["offgridwind"]), 'WindVN': int(qdict["offgridvirtualwind"]), 'TotalGen': int(qdict["offgridtotal"]), 'TotalConspN': int(qdict["offgridconsumption"]), 'SolarVNGenTotalId': [], 'SolarVNGenSolarId': [], 'SolarVNGenWindId': [], 'WindVNGenTotalId': [], 'WindVNGenSolarId': [], 'WindVNGenWindId': []}
            listOfFormStations.append(qdict["offGridStation"].replace(" ", ""))

        # add virtual solar
        for stationKey in listOfFormStations:
            offGridStationsDef[stationKey]['SolarVNGenTotalId'] = []
            offGridStationsDef[stationKey]['SolarVNGenSolarId'] = []
            offGridStationsDef[stationKey]['SolarVNGenWindId'] = []

            for i in range(offGridStationsDef[stationKey]['WindVN']):
                offGridStationsDef[stationKey]['SolarVNGenTotalId'].append([])
                for k in range(offGridStationsDef[stationKey]['TotalGen']):
                    if "V"+ str(i) +"Solar"+ stationKey.replace(" ", "") +"TGen"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['SolarVNGenTotalId'][i].append(k)

                offGridStationsDef[stationKey]['SolarVNGenSolarId'].append([])
                for k in range(offGridStationsDef[stationKey]['SolarN']):
                    if "V"+ str(i) +"Solar"+ stationKey.replace(" ", "") +"Solar"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['SolarVNGenSolarId'][i].append(k)

                offGridStationsDef[stationKey]['SolarVNGenWindId'].append([])
                for k in range(offGridStationsDef[stationKey]['WindN']):
                    if "V"+ str(i) +"Solar"+ stationKey.replace(" ", "") +"Wind"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['SolarVNGenWindId'][i].append(k)

        # add virtual wind
        for stationKey in listOfFormStations:
            offGridStationsDef[stationKey]['WindVNGenTotalId'] = []
            offGridStationsDef[stationKey]['WindVNGenSolarId'] = []
            offGridStationsDef[stationKey]['WindVNGenWindId'] = []

            for i in range(offGridStationsDef[stationKey]['WindVN']):
                offGridStationsDef[stationKey]['WindVNGenTotalId'].append([])
                for k in range(offGridStationsDef[stationKey]['TotalGen']):
                    if "V"+ str(i) +"Wind"+ stationKey.replace(" ", "") +"TGen"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['WindVNGenTotalId'][i].append(k)

                offGridStationsDef[stationKey]['WindVNGenSolarId'].append([])
                for k in range(offGridStationsDef[stationKey]['SolarN']):
                    if "V"+ str(i) +"Wind"+ stationKey.replace(" ", "") +"Solar"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['WindVNGenSolarId'][i].append(k)

                offGridStationsDef[stationKey]['WindVNGenWindId'].append([])
                for k in range(offGridStationsDef[stationKey]['WindN']):
                    if "V"+ str(i) +"Wind"+ stationKey.replace(" ", "") +"Wind"+ str(k) in qdict:
                        offGridStationsDef[stationKey]['WindVNGenWindId'][i].append(k)

        # save 2 defitions files relative to off-grid stations
        with open(u"./data/energy_manager_offgrid.json", u"w") as f:  # Edit: change name of json file
            json.dump(offGridStationsDef, f)  # save to file
        lockOffGridStationsDef.release()

        raise web.seeother(u"/off-grid-location")

class home(ProtectedPage):
    """
    Load an html page for entering plugin settings.
    """

    def GET(self):
        global lockOffGridStationsDef, offGridStationsDef

        lockOffGridStationsDef.acquire()
        tmpDataOffGrid = copy.deepcopy(offGridStationsDef)
        lockOffGridStationsDef.release()

        return template_render.energy_manager_home(tmpDataOffGrid)  # open settings page

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

class offgrid_initial_data(ProtectedPage):
    # send data of initial data
    def GET(self):
        qdict = web.input()

        if "OffGridName" not in qdict:
            return "NONE"

        if withDBLogger:
            dbDefinitions = db_logger_read_definitions()
            return get_init_value(dbDefinitions, qdict["OffGridName"])
        else:
            return "NONE"

class offgrid_sensor(ProtectedPage):
    # receide data from off-grid
    def GET(self):
        qdict = web.input()

        # insert date time receive and add to queu to precess
        qdict["DateTime"] = datetime.now()
        commandsOffGridQueu.put(qdict)

        return "||Ok offgrid"

def sunpos(when, location, refraction):
# Extract the passed data
    year, month, day, hour, minute, second, timezone = when
    latitude, longitude = location
# Math typing shortcuts
    rad, deg = math.radians, math.degrees
    sin, cos, tan = math.sin, math.cos, math.tan
    asin, atan2 = math.asin, math.atan2
# Convert latitude and longitude to radians
    rlat = rad(latitude)
    rlon = rad(longitude)
# Decimal hour of the day at Greenwich
    greenwichtime = hour - timezone + minute / 60 + second / 3600
# Days from J2000, accurate from 1901 to 2099
    daynum = (
        367 * year
        - 7 * (year + (month + 9) // 12) // 4
        + 275 * month // 9
        + day
        - 730531.5
        + greenwichtime / 24
    )
# Mean longitude of the sun
    mean_long = daynum * 0.01720279239 + 4.894967873
# Mean anomaly of the Sun
    mean_anom = daynum * 0.01720197034 + 6.240040768
# Ecliptic longitude of the sun
    eclip_long = (
        mean_long
        + 0.03342305518 * sin(mean_anom)
        + 0.0003490658504 * sin(2 * mean_anom)
    )
# Obliquity of the ecliptic
    obliquity = 0.4090877234 - 0.000000006981317008 * daynum
# Right ascension of the sun
    rasc = atan2(cos(obliquity) * sin(eclip_long), cos(eclip_long))
# Declination of the sun
    decl = asin(sin(obliquity) * sin(eclip_long))
# Local sidereal time
    sidereal = 4.894961213 + 6.300388099 * daynum + rlon
# Hour angle of the sun
    hour_ang = sidereal - rasc
# Local elevation of the sun
    elevation = asin(sin(decl) * sin(rlat) + cos(decl) * cos(rlat) * cos(hour_ang))
# Local azimuth of the sun
    azimuth = atan2(
        -cos(decl) * cos(rlat) * sin(hour_ang),
        sin(decl) - sin(rlat) * sin(elevation),
    )
# Convert azimuth and elevation to degrees
    azimuth = into_range(deg(azimuth), 0, 360)
    elevation = into_range(deg(elevation), -180, 180)
# Refraction correction (optional)
    if refraction:
        targ = rad((elevation + (10.3 / (elevation + 5.11))))
        elevation += (1.02 / tan(targ)) / 60
# Return azimuth and elevation in degrees
    return (round(azimuth, 2), round(elevation, 2))
def into_range(x, range_min, range_max):
    shiftedx = x - range_min
    delta = range_max - range_min
    return (((shiftedx % delta) + delta) % delta) + range_min

class offgrid_day_night(ProtectedPage):
    def GET(self):
        qdict = web.input()

        # https://levelup.gitconnected.com/python-sun-position-for-solar-energy-and-research-7a4ead801777

        latVal = 0
        logVal = 0

        lockOffGridStationsDef.acquire()
        if "DeviceRef" in qdict and qdict["DeviceRef"] in offGridStationsDef:
            latVal = offGridStationsDef[qdict["DeviceRef"]]["Lat"]
            logVal = offGridStationsDef[qdict["DeviceRef"]]["Log"]
        lockOffGridStationsDef.release()

        location = (latVal, logVal)

        currentTime = datetime.now(timezone.utc)

        when = (currentTime.year, currentTime.month, currentTime.day, currentTime.hour, currentTime.minute, 0, 0)

        azimuth, elevation = sunpos(when, location, True)

        if elevation > 0:
            return "|DAY|"

        return "|NIGHT|"

class offgrid_ged_current_val(ProtectedPage):
    def GET(self):
        global offGridStationsDef

        qdict = web.input()

        dataOut = ""

        if "OffGridRef" in qdict and "SourceName" in qdict:
            # variables associated to solar
            totalSolarC = 0
            totalSolarP = 0
            totalSolarE = 0

            # variables associated to wind
            totalWindC = 0
            totalWindE = 0
            totalWindP = 0

            # variables associated to total generation
            totalCTGen = 0
            totalPTGen = 0
            totalETGen = 0

            # variables associated to consuption
            totalCConsp = 0
            totalPConsp = 0
            totalEConsp = 0

            lockOffGridStationsDef.acquire()
            offGridDateOnDemandLock.acquire()

            if qdict["OffGridRef"] in offGridDateOnDemand:
                if qdict["SourceName"] in offGridDateOnDemand[qdict["OffGridRef"]]:
                    dataOut = str(round(offGridDateOnDemand[qdict["OffGridRef"]][qdict["SourceName"]], 2))
                if qdict["SourceName"] == "CSOLAR":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["SolarN"]):
                        totalSolarC = totalSolarC + offGridDateOnDemand[qdict["OffGridRef"]]["CSOLAR"+ str(i + 1)]
                    dataOut = str(round(totalSolarC, 2))
                if qdict["SourceName"] == "PSOLAR" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["SolarN"]):
                        totalSolarP = totalSolarP + offGridDateOnDemand[qdict["OffGridRef"]]["PSOLAR"+ str(i + 1)]
                    dataOut = str(round(totalSolarP, 2))
                if qdict["SourceName"] == "ESOLAR":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["SolarN"]):
                        totalSolarE = totalSolarE + offGridDateOnDemand[qdict["OffGridRef"]]["ESOLAR"+ str(i + 1)]
                    dataOut = str(round(totalSolarE, 2))
                if qdict["SourceName"] == "CWIND":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["WindN"]):
                        totalWindC = totalWindC + offGridDateOnDemand[qdict["OffGridRef"]]["CWIND"+ str(i + 1)]
                    dataOut = str(round(totalWindC, 2))
                if qdict["SourceName"] == "EWIND":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["WindN"]):
                        totalWindE = totalWindE + offGridDateOnDemand[qdict["OffGridRef"]]["EWIND"+ str(i + 1)]
                    dataOut = str(round(totalWindE, 2))
                if qdict["SourceName"] == "PWIND" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["WindN"]):
                        totalWindP = totalWindP + offGridDateOnDemand[qdict["OffGridRef"]]["PWIND"+ str(i + 1)]
                    dataOut = str(round(totalWindP, 2))
                if qdict["SourceName"] == "CGENTOTAL":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalGen"]):
                        totalCTGen = totalCTGen + offGridDateOnDemand[qdict["OffGridRef"]]["CGENTOTAL"+ str(i + 1)]
                    dataOut = str(round(totalCTGen, 2))
                if qdict["SourceName"] == "PGENTOTAL":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalGen"]):
                        totalPTGen = totalPTGen + offGridDateOnDemand[qdict["OffGridRef"]]["PGENTOTAL"+ str(i + 1)]
                    dataOut = str(round(totalPTGen, 2))
                if qdict["SourceName"] == "EGENTOTAL":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalGen"]):
                        totalETGen = totalETGen + offGridDateOnDemand[qdict["OffGridRef"]]["EGENTOTAL"+ str(i + 1)]
                    dataOut = str(round(totalETGen, 2))
                if qdict["SourceName"] == "CCONSP":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalConspN"]):
                        totalCConsp = totalCConsp + offGridDateOnDemand[qdict["OffGridRef"]]["CCONSP"+ str(i + 1)]
                        dataOut = str(round(totalCConsp, 2))
                if qdict["SourceName"] == "PCONSP" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalConspN"]):
                        totalPConsp = totalPConsp + offGridDateOnDemand[qdict["OffGridRef"]]["PCONSP"+ str(i + 1)]
                        dataOut = str(round(totalPConsp, 2))
                if qdict["SourceName"] == "ECONSP":
                    for i in range(offGridStationsDef[qdict["OffGridRef"]]["TotalConspN"]):
                        totalEConsp = totalEConsp + offGridDateOnDemand[qdict["OffGridRef"]]["ECONSP"+ str(i + 1)]
                        dataOut = str(round(totalEConsp, 2))

                # check virtual solar
                solarPVirtual = 0
                solarPValid = False

                solarEVirtual = 0
                solarEValid = False

                for i in range(offGridStationsDef[qdict["OffGridRef"]]["SolarVN"]):
                    if qdict["SourceName"] == "VPSOLAR"+ str(i + 1) or qdict["SourceName"] == "VSOLARPT" or qdict["SourceName"] == "VSOLARPGT" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                        solarPValid = True

                        # Total generation sensors
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenTotalId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenTotalId"][i][k]
                            solarPVirtual = solarPVirtual + offGridDateOnDemand[qdict["OffGridRef"]]["PGENTOTAL"+ str(idxGenTotal + 1)]

                        # Other solar sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenSolarId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenSolarId"][i][k]
                            solarPVirtual = solarPVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["PSOLAR"+ str(idxGenTotal + 1)]

                        # Other wind sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenWindId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenWindId"][i][k]
                            solarPVirtual = solarPVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["PWIND"+ str(idxGenTotal + 1)]

                        dataOut = str(round(solarPVirtual, 2))
                    if qdict["SourceName"] == "VESOLAR"+ str(i + 1)  or qdict["SourceName"] == "VSOLARET" or qdict["SourceName"] == "VSOLAREGT" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                        solarEValid = True
                        
                        # Total generation sensors
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenTotalId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenTotalId"][i][k]
                            solarEVirtual = solarEVirtual + offGridDateOnDemand[qdict["OffGridRef"]]["EGENTOTAL"+ str(idxGenTotal + 1)]

                        # Other solar sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenSolarId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenSolarId"][i][k]
                            solarEVirtual = solarEVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["ESOLAR"+ str(idxGenTotal + 1)]

                        # Other wind sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenWindId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["SolarVNGenWindId"][i][k]
                            solarEVirtual = solarEVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["EWIND"+ str(idxGenTotal + 1)]

                if solarPValid:
                    dataOut = str(round(solarPVirtual, 2))

                if solarEValid:
                    dataOut = str(round(solarEVirtual, 2))

                # check virtual wind
                windPVirtual = 0
                windPValid = False

                windEVirtual = 0
                windEValid = False

                for i in range(offGridStationsDef[qdict["OffGridRef"]]["WindVN"]):
                    if qdict["SourceName"] == "VPWIND"+ str(i + 1) or qdict["SourceName"] == "VWINDPT" or qdict["SourceName"] == "VWINDPGT" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                        windPValid = True

                        # Total generation sensors
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenTotalId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenTotalId"][i][k]
                            windPVirtual = windPVirtual + offGridDateOnDemand[qdict["OffGridRef"]]["PGENTOTAL"+ str(idxGenTotal + 1)]

                        # Other wind sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenSolarId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenSolarId"][i][k]
                            windPVirtual = windPVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["PSOLAR"+ str(idxGenTotal + 1)]

                        # Other wind sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenWindId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenWindId"][i][k]
                            windPVirtual = windPVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["PWIND"+ str(idxGenTotal + 1)]

                        dataOut = str(round(windPVirtual, 2))
                    if qdict["SourceName"] == "VEWIND"+ str(i + 1) or qdict["SourceName"] == "VWINDET" or qdict["SourceName"] == "VWINDEGT" or qdict["SourceName"] == "PPRODUCTION" or qdict["SourceName"] == "PCONSUPTION" or qdict["SourceName"] == "PBATTERY":
                        windEValid = True
                        
                        # Total generation sensors
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenTotalId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenTotalId"][i][k]
                            windEVirtual = windEVirtual + offGridDateOnDemand[qdict["OffGridRef"]]["EGENTOTAL"+ str(idxGenTotal + 1)]

                        # Other solar sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenSolarId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenSolarId"][i][k]
                            windEVirtual = windEVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["ESOLAR"+ str(idxGenTotal + 1)]

                        # Other wind sensor
                        for k in range(len(offGridStationsDef[qdict["OffGridRef"]]["WindVNGenWindId"][i])):
                            idxGenTotal = offGridStationsDef[qdict["OffGridRef"]]["WindVNGenWindId"][i][k]
                            windEVirtual = windEVirtual - offGridDateOnDemand[qdict["OffGridRef"]]["EWIND"+ str(idxGenTotal + 1)]
                
                if windPValid:
                    dataOut = str(round(windPVirtual, 2))

                if windEValid:
                    dataOut = str(round(windEVirtual, 2))

                if qdict["SourceName"] == "PCONSUPTION":
                    dataOut = str(round(totalPConsp, 2))
                elif qdict["SourceName"] == "PPRODUCTION":
                    dataOut = str(round(totalSolarP + solarPVirtual + totalWindP + windPVirtual, 2))
                elif qdict["SourceName"] == "PBATTERY":
                    dataOut = str(round(totalSolarP + solarPVirtual + totalWindP + windPVirtual - totalPConsp, 2))

            # estimate 

            lockOffGridStationsDef.release()
            offGridDateOnDemandLock.release()

        return dataOut
