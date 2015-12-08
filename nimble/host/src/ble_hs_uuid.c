/**
 * Copyright (c) 2015 Runtime Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 * 
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <assert.h>
#include <inttypes.h>
#include <string.h>
#include <errno.h>
#include "os/os_mbuf.h"
#include "nimble/ble.h"
#include "host/ble_hs_uuid.h"

static uint8_t ble_hs_uuid_base[16] = {
    0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80,
    0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
};

/**
 * Attempts to convert the supplied 128-bit UUID into its shortened 16-bit
 * form.
 *
 * @return                          Positive 16-bit unsigned integer on
 *                                      success;
 *                                  0 if the UUID could not be converted.
 */
uint16_t
ble_hs_uuid_16bit(void *uuid128)
{
    uint16_t uuid16;
    uint8_t *u8ptr;
    int rc;

    u8ptr = uuid128;

    /* The UUID can only be converted if the final 96 bits of its big endian
     * representation are equal to the base UUID.
     */
    rc = memcmp(u8ptr, ble_hs_uuid_base, sizeof ble_hs_uuid_base - 4);
    if (rc != 0) {
        return 0;
    }

    if (u8ptr[14] != 0 || u8ptr[15] != 0) {
        /* This UUID has a 32-bit form, but not a 16-bit form. */
        return 0;
    }

    uuid16 = le16toh(u8ptr + 12);
    if (uuid16 == 0) {
        return 0;
    }

    return uuid16;
}

int
ble_hs_uuid_from_16bit(uint16_t uuid16, void *uuid128)
{
    uint8_t *u8ptr;

    if (uuid16 == 0) {
        return EINVAL;
    }

    u8ptr = uuid128;

    memcpy(u8ptr, ble_hs_uuid_base, 16);
    htole16(u8ptr + 12, uuid16);

    return 0;
}

int
ble_hs_uuid_append(struct os_mbuf *om, void *uuid128)
{
    uint16_t uuid16;
    void *buf;
    int rc;

    uuid16 = ble_hs_uuid_16bit(uuid128);
    if (uuid16 != 0) {
        buf = os_mbuf_extend(om, 2);
        if (buf == NULL) {
            return ENOMEM;
        }

        htole16(buf, uuid16);
    } else {
        rc = os_mbuf_append(om, uuid128, 16);
        if (rc != 0) {
            return ENOMEM;
        }
    }

    return 0;
}

int
ble_hs_uuid_extract(struct os_mbuf *om, int off, void *uuid128)
{
    uint16_t uuid16;
    int remlen;
    int rc;

    remlen = OS_MBUF_PKTHDR(om)->omp_len;
    switch (remlen) {
    case 2:
        rc = os_mbuf_copydata(om, off, 2, &uuid16);
        assert(rc == 0);

        uuid16 = le16toh(&uuid16);
        rc = ble_hs_uuid_from_16bit(uuid16, uuid128);
        if (rc != 0) {
            return rc;
        }
        return 0;

    case 16:
        rc = os_mbuf_copydata(om, off, 16, uuid128);
        assert(rc == 0);
        return 0;

    default:
        return EMSGSIZE;
    }
}
