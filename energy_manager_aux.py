import requests

from urllib.request import urlopen
import json
import math

import gv  # Get access to SIP's settings, gv = global variables

def get_raw_reading_shelly_em_generic(shellyIp, numberOfChannels, useSecure):
    if useSecure:
        url = "https://" + shellyIp + "/status"
    else:
        url = "http://" + shellyIp + "/status"

    # instant power for each phase
    power = [0.0] * numberOfChannels

    # power factor for each phase
    pf = [0.0] * numberOfChannels

    # voltage for each phase
    voltage = [0.0] * numberOfChannels

    # current for each phase
    current = [0.0] * numberOfChannels

    # total accumulate for each phase
    accCons = [0.0] * numberOfChannels

    # Total accumulate send each phase
    accSend = [0.0] * numberOfChannels

    hashOfData = {'power': power, 'pf': pf, 'voltage': voltage, 'current': current, 'accCons': accCons, 'accSend': accSend, 'ValidReading': True}

    try:
        response = urlopen(url)
    except Exception:
        hashOfData['ValidReading'] = False
        return hashOfData
    dataJson = json.loads(response.read())

    if 'emeters' in dataJson:
        if len(dataJson['emeters']) == 3:
            # read intant power for each phase
            for i in range(3):
                power[i] = float(dataJson['emeters'][i]['power'])

            # read power factor for each phase
            for i in range(3):
                pf[i] = float(dataJson['emeters'][i]['pf'])

            # read voltage for each phase
            for i in range(3):
                voltage[i] = float(dataJson['emeters'][i]['voltage'])

            # read current for each phase
            for i in range(3):
                current[i] = float(dataJson['emeters'][i]['current'])

            # read accumulative receive for each phase
            for i in range(3):
                accCons[i] = float(dataJson['emeters'][i]['total'])

            # read accumulative send for each phase
            for i in range(3):
                accSend[i] = float(dataJson['emeters'][i]['total_returned'])

    hashOfData = {'power': power, 'pf': pf, 'voltage': voltage, 'current': current, 'accCons': accCons, 'accSend': accSend, 'ValidReading': True}
    return hashOfData

def get_raw_reading_shelly_em(shellyIp, useSecure = False):
    return get_raw_reading_shelly_em_generic(shellyIp, 2, useSecure)

def get_raw_reading_shelly_em3(shellyIp, useSecure = False):
    return get_raw_reading_shelly_em_generic(shellyIp, 3, useSecure)

def get_meter_values(list2Read, repeatedReading):
    # Net meter
    totalPower = 0.0
    totalEnergytAcc = 0.0
    totalEnergyAccGen = 0.0

    validReading = True

    metterReading = []
    for currMeter in list2Read:
        if len(currMeter) == 2:
            if currMeter[1] == 'shellyEM3':
                if currMeter[0] in repeatedReading:
                    newReading = repeatedReading[currMeter[0]]
                    metterReading.append(newReading)
                else:
                    newReading = get_raw_reading_shelly_em3(currMeter[0])
                    # TODO: Check when fail reading
                    metterReading.append(newReading)
                    repeatedReading[currMeter[0]] = newReading
                totalPower = totalPower + sum(newReading['power'])
                totalEnergytAcc = totalEnergytAcc + sum(newReading['accCons'])
                totalEnergyAccGen = totalEnergyAccGen + sum(newReading['accSend'])

                validReading = newReading['ValidReading'] and validReading
            elif currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_1' or currMeter[1] == 'shellyEM3_2' or currMeter[1] == 'shellyEM3_3':
                if currMeter[0] in repeatedReading:
                    metterReading.append(repeatedReading[currMeter[0]])
                else:
                    newReading = get_raw_reading_shelly_em(currMeter[0])
                    # TODO: Check when fail reading
                    metterReading.append(newReading)
                    repeatedReading[currMeter[0]] = newReading

                idxClip = -1
                if currMeter[1] == 'shellyEM_1' or currMeter[1] == 'shellyEM3_1':
                    idxClip = 0
                elif currMeter[1] == 'shellyEM_2' or currMeter[1] == 'shellyEM3_2':
                    idxClip = 1
                elif currMeter[1] == 'shellyEM3_3':
                    idxClip = 2
                if idxClip >= 0:
                    totalPower = totalPower + newReading['power'][idxClip]
                    totalEnergytAcc = totalEnergytAcc + newReading['accCons'][idxClip]
                    totalEnergyAccGen = totalEnergyAccGen + newReading['accSend'][idxClip]

                validReading = newReading['ValidReading'] and validReading

    return totalPower, totalEnergytAcc, totalEnergyAccGen, metterReading, validReading

def sendLocalHTTPRequest(urlName, arguments):
    try:
        if gv.sd['htip'] == '::':
            requests.get("http://127.0.0.1" + ":" + str(gv.sd['htp']) +"/" + urlName + arguments)
        else:
            requests.get("http://"+ str(gv.sd['htip']) + ":" + str(gv.sd['htp']) +"/" + urlName + arguments)

        return True
    except:
        return False

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

