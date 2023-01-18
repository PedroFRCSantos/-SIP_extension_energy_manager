from urllib.request import urlopen
import json

def get_raw_reading_shelly_em3(shellyIp, useSecure = False):
    if useSecure:
        url = "https://" + shellyIp + "/status"
    else:
        url = "http://" + shellyIp + "/status"

    # instant power for each phase
    power = {0.0, 0.0, 0.0}

    # power factor for each phase
    pf = {0.0, 0.0, 0.0}

    # voltage for each phase
    voltage = {0.0, 0.0, 0.0}

    # current for each phase
    current = {0.0, 0.0, 0.0}

    # total accumulate for each phase
    accCons = {0.0, 0.0, 0.0}

    # Total accumulate send each phase
    accSend = {0.0, 0.0, 0.0}

    response = urlopen(url)
    dataJson = json.loads(response.read())

    if 'emeters' in dataJson:
        if len(dataJson['emeters']) == 3:
            # read intant power for each phase
            for i in range(3):
                power[i] = float(dataJson['emeters'][i]['power'])

            # read power factor for each phase
            for i in range(3):
                pf = float(dataJson['emeters'][i]['pf'])

            # read voltage for each phase
            for i in range(3):
                voltage = float(dataJson['emeters'][i]['voltage'])

            # read current for each phase
            for i in range(3):
                current = float(dataJson['emeters'][i]['current'])

            # read accumulative receive for each phase
            for i in range(3):
                accCons = float(dataJson['emeters'][i]['total'])

            # read accumulative send for each phase
            for i in range(3):
                accSend = float(dataJson['emeters'][i]['total_returned'])

    return power, pf, voltage, current, accCons, accSend
