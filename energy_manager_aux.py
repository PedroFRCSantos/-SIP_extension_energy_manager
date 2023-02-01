from urllib.request import urlopen
import json

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

    response = urlopen(url)
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

    hashOfData = {'power': power, 'pf': pf, 'voltage': voltage, 'current': current, 'accCons': accCons, 'accSend': accSend}
    return hashOfData

def get_raw_reading_shelly_em(shellyIp, useSecure = False):
    return get_raw_reading_shelly_em_generic(shellyIp, 2, useSecure)

def get_raw_reading_shelly_em3(shellyIp, useSecure = False):
    return get_raw_reading_shelly_em_generic(shellyIp, 3, useSecure)

