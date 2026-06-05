/*
 * Intel MIC Platform Software Stack (MPSS)
 *
 * Copyright 2010-2012 Intel Corporation.
 *
 * This library is free software; you can redistribute it and/or modify
 * it under the terms of the GNU Lesser General Public License as
 * published by the Free Software Foundation, version 2.1.
 *
 * This library is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
 * 02110-1301 USA.
 *
 * Disclaimer: The codes contained in these modules may be specific to
 * the Intel Software Development Platform codenamed: Knights Ferry, and
 * the Intel product codenamed: Knights Corner, and are not backward
 * compatible with other Intel products. Additionally, Intel will NOT
 * support the codes or instruction set in future products.
 *
 * Intel offers no warranty of any kind regarding the code. This code is
 * licensed on an "AS IS" basis and Intel is not obligated to provide
 * any support, assistance, installation, training, or other services of
 * any kind. Intel is also not obligated to provide any updates,
 * enhancements or extensions. Intel specifically disclaims any warranty
 * of merchantability, non-infringement, fitness for any particular
 * purpose, and any other warranty.
 *
 * Further, Intel disclaims all liability of any kind, including but not
 * limited to liability for infringement of any proprietary rights,
 * relating to the use of the code, even if Intel is notified of the
 * possibility of such liability. Except as expressly stated in an Intel
 * license agreement provided with this code and agreed upon with Intel,
 * no license, express or implied, by estoppel or otherwise, to any
 * intellectual property rights is granted herein.
 */

/*! \file miclib.h
 *  Vendored Intel MPSS (Knights Corner) management library API.
 *
 *  Used only by the host_mic collector (mic.c). This is not monitor schema
 *  output: emitted MIC stats use host_mic and lowercase snake_case KEYS there.
 *
 *  Unless documented otherwise for a specific call, output pointer parameters
 *  must be non-NULL; mic_device handles from mic_open_device must be passed to
 *  mic_close_device. mic_get_error_string() returns a static message string and
 *  is never NULL.
 */

#ifndef MICLIB_INCLUDE_MICLIB_H_
#define MICLIB_INCLUDE_MICLIB_H_

#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <sys/types.h>

/* Device Opaque object data structures */

struct mic_device;
struct mic_devices_list;
struct mic_device_mem;
struct mic_processor_info;
struct mic_thermal_info;
struct mic_version_info;
struct mic_cores_info;
struct mic_cores_jiff;
struct mic_cores_util;
struct mic_power_util_info;
struct mic_memory_util_info;
struct mic_core_util;
struct mic_power_limit;
struct mic_pci_config;
struct mic_flash_op;
struct mic_scif_desc;
struct mic_flash_status_info;
struct mic_turbo_info;
struct mic_throttle_state_info;
struct mic_uos_pm_config;

/* Values returned in mic_get_device_type() */
#define KNC_ID    (1)

/*
 * The following are the error codes returned by the API
 */
typedef enum _error_code {
        E_MIC_SUCCESS = 0,
        E_MIC_INVAL = 1,
        E_MIC_ACCESS,
        E_MIC_NOENT,
        E_MIC_UNSUPPORTED_DEV,
        E_MIC_NOT_IMPLEMENTED,
        E_MIC_DRIVER_INIT,
        E_MIC_DRIVER_NOT_LOADED,
        E_MIC_IOCTL_FAILED,
        E_MIC_ERROR_NOT_FOUND,
        E_MIC_NOMEM,
        E_MIC_RANGE,
        E_MIC_INTERNAL,
        E_MIC_SYSTEM,
        E_MIC_SCIF_ERROR,
        E_MIC_STACK
} mic_error_code;

#define FLASH_OP_STATUS    (0x1 << 4)
#define SMC_OP_STATUS      (0x2 << 4)
typedef enum _flash_status {
        FLASH_OP_IDLE = 0,
        FLASH_OP_INVALID,
        FLASH_OP_IN_PROGRESS = (FLASH_OP_STATUS | 1),
        FLASH_OP_COMPLETED,
        FLASH_OP_FAILED,
        FLASH_OP_AUTH_FAILED,
        SMC_OP_IN_PROGRESS = (SMC_OP_STATUS | 1),
        SMC_OP_COMPLETED,
        SMC_OP_FAILED,
        SMC_OP_AUTH_FAILED
} mic_flash_status;

#define FLASH_OP(status)    ((status) & FLASH_OP_STATUS)
#define SMC_OP(status)      ((status) & SMC_OP_STATUS)


/* Error handling */
const char *mic_get_error_string();
int mic_clear_error_string();

/* Maintenance mode start/stop */
int mic_enter_maint_mode(struct mic_device *mdh);
int mic_leave_maint_mode(struct mic_device *mdh);
int mic_in_maint_mode(struct mic_device *mdh, int *mode);
int mic_in_ready_state(struct mic_device *mdh, int *state);
int mic_get_post_code(struct mic_device *mdh, char *postcode, size_t *bufsize);


/* Flash operations */
int mic_flash_size(struct mic_device *mdh, size_t *size);
int mic_flash_active_offs(struct mic_device *mdh, off_t *active);
int mic_flash_update_start(struct mic_device *mdh, void *buf, size_t bufsize,
                           struct mic_flash_op **desc);
int mic_flash_update_done(struct mic_flash_op *desc);
int mic_flash_read_start(struct mic_device *mdh, void *buf, size_t bufsize,
                         struct mic_flash_op **desc);
int mic_flash_read_done(struct mic_flash_op *desc);
int mic_set_ecc_mode_start(struct mic_device *mdh, uint16_t ecc_enabled,
                struct mic_flash_op **desc);
int mic_set_ecc_mode_done(struct mic_flash_op *desc);

int mic_get_flash_status_info(struct mic_flash_op *desc,
                              struct mic_flash_status_info **status);
int mic_get_progress(struct mic_flash_status_info *status, uint32_t *percent);
int mic_get_status(struct mic_flash_status_info *status, int *cmd_status);
int mic_get_ext_status(struct mic_flash_status_info *status, int *ext_status);
int mic_free_flash_status_info(struct mic_flash_status_info *status);

int mic_flash_version(struct mic_device *mdh, void *buf, char *str, size_t size);
int mic_get_flash_vendor_device(struct mic_device *mdh, char *buf, size_t *size);

/* pci configuration */
int mic_get_pci_config(struct mic_device *mdh, struct mic_pci_config **conf);
int mic_get_bus_number(struct mic_pci_config *conf, uint16_t *bus_no);
int mic_get_device_number(struct mic_pci_config *conf, uint16_t *dev_no);
int mic_get_vendor_id(struct mic_pci_config *conf, uint16_t *id);
int mic_get_device_id(struct mic_pci_config *conf, uint16_t *id);
int mic_get_revision_id(struct mic_pci_config *conf, uint8_t *id);
int mic_get_subsystem_id(struct mic_pci_config *conf, uint16_t *id);
int mic_get_link_speed(struct mic_pci_config *conf, char *speed, size_t *size);
int mic_get_link_width(struct mic_pci_config *conf, uint32_t *width);
int mic_get_max_payload(struct mic_pci_config *conf, uint32_t *payload);
int mic_get_max_readreq(struct mic_pci_config *conf, uint32_t *readreq);
int mic_free_pci_config(struct mic_pci_config *conf);

/* Operations host platform */
int mic_get_devices(struct mic_devices_list **devices);
int mic_free_devices(struct mic_devices_list *devices);
int mic_get_ndevices(struct mic_devices_list *devices, int *ndevices);
int mic_get_device_at_index(struct mic_devices_list *devices, int index,
                            int *device);

/* Open close device and device type */
int mic_open_device(struct mic_device **device, uint32_t device_num);
int mic_close_device(struct mic_device *device);
int mic_get_device_type(struct mic_device *device, uint32_t *device_type);
const char *mic_get_device_name(struct mic_device *device);

/* thermal info */
int mic_get_thermal_info(struct mic_device *mdh,
                         struct mic_thermal_info **thermal);
int mic_get_smc_hwrevision(struct mic_thermal_info *thermal, char *rev,
                           size_t *size);
int mic_get_smc_fwversion(struct mic_thermal_info *thermal, char *ver,
                          size_t *size);
int mic_is_smc_boot_loader_ver_supported(struct mic_thermal_info *thermal,
                                         int *supported);
int mic_get_smc_boot_loader_ver(struct mic_thermal_info *thermal, char *ver,
                                size_t *size);
int mic_get_fsc_status(struct mic_thermal_info *thermal, uint32_t *status);
int mic_get_die_temp(struct mic_thermal_info *thermal, uint32_t *temp);
int mic_is_die_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_gddr_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_gddr_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_fanin_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_fanin_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_fanout_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_fanout_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_vccp_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_vccp_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_vddg_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_vddg_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_vddq_temp(struct mic_thermal_info *thermal, uint16_t *temp);
int mic_is_vddq_temp_valid(struct mic_thermal_info *thermal, int *valid);
int mic_get_fan_rpm(struct mic_thermal_info *thermal, uint32_t *rpm);
int mic_get_fan_pwm(struct mic_thermal_info *thermal, uint32_t *pwm);
int mic_free_thermal_info(struct mic_thermal_info *thermal);

/* device memory info */
int mic_get_memory_info(struct mic_device *mdh, struct mic_device_mem **meminfo);
int mic_get_memory_vendor(struct mic_device_mem *mem, char *vendor,
                          size_t *bufsize);
int mic_get_memory_revision(struct mic_device_mem *mem, uint32_t *revision);
int mic_get_memory_density(struct mic_device_mem *mem, uint32_t *density);
int mic_get_memory_size(struct mic_device_mem *mem, uint32_t *size);
int mic_get_memory_speed(struct mic_device_mem *mem, uint32_t *speed);
int mic_get_memory_type(struct mic_device_mem *mem, char *type, size_t *bufsize);
int mic_get_memory_frequency(struct mic_device_mem *mem, uint32_t *buf);
int mic_get_memory_voltage(struct mic_device_mem *mem, uint32_t *buf);
int mic_get_ecc_mode(struct mic_device_mem *mem, uint16_t *ecc);
int mic_free_memory_info(struct mic_device_mem *mem);

/* processor info */
int mic_get_processor_info(struct mic_device *mdh,
                           struct mic_processor_info **processor);
int mic_get_processor_model(struct mic_processor_info *processor,
                            uint16_t *model,
                            uint16_t *model_ext);
int mic_get_processor_family(struct mic_processor_info *processor,
                             uint16_t *family,
                             uint16_t *family_ext);
int mic_get_processor_type(struct mic_processor_info *processor, uint16_t *type);
int mic_get_processor_steppingid(struct mic_processor_info *processor,
                                 uint32_t *id);
int mic_get_processor_stepping(struct mic_processor_info *processor,
                               char *stepping,
                               size_t *size);
int mic_free_processor_info(struct mic_processor_info *processor);

/* uos core info */
int mic_get_cores_info(struct mic_device *mdh, struct mic_cores_info **cores);
int mic_get_cores_count(struct mic_cores_info *core, uint32_t *num_cores);
int mic_get_cores_voltage(struct mic_cores_info *core, uint32_t *voltage);
int mic_get_cores_frequency(struct mic_cores_info *core, uint32_t *frequency);
int mic_free_cores_info(struct mic_cores_info *cores);

/* version info*/
int mic_get_version_info(struct mic_device *mdh,
                         struct mic_version_info **version);
int mic_get_uos_version(struct mic_version_info *version, char *uos,
                        size_t *size);
int mic_get_flash_version(struct mic_version_info *version, char *flash,
                          size_t *size);
int mic_get_fsc_strap(struct mic_version_info *version, char *strap,
                      size_t *size);
int mic_free_version_info(struct mic_version_info *version);

/* silicon SKU */
int mic_get_silicon_sku(struct mic_device *mdh, char *sku, size_t *size);

/* serial number */
int mic_get_serial_number(struct mic_device *mdh, char *serial, size_t *size);

/* power utilization info */
int mic_get_power_utilization_info(struct mic_device *mdh,
                                   struct mic_power_util_info **power);
int mic_get_total_power_readings_w0(struct mic_power_util_info *power,
                                    uint32_t *pwr);
int mic_get_total_power_sensor_sts_w0(struct mic_power_util_info *power,
                                      uint32_t *sts);
int mic_get_total_power_readings_w1(struct mic_power_util_info *power,
                                    uint32_t *pwr);
int mic_get_total_power_sensor_sts_w1(struct mic_power_util_info *power,
                                      uint32_t *sts);
int mic_get_inst_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_inst_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_max_inst_power_readings(struct mic_power_util_info *power,
                                    uint32_t *pwr);
int mic_get_max_inst_power_sensor_sts(struct mic_power_util_info *power,
                                      uint32_t *sts);
int mic_get_pcie_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_pcie_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_c2x3_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_c2x3_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_c2x4_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_c2x4_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_vccp_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_vccp_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_vccp_current_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vccp_current_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_get_vccp_voltage_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vccp_voltage_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_get_vddg_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_vddg_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_vddg_current_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vddg_current_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_get_vddg_voltage_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vddg_voltage_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_get_vddq_power_readings(struct mic_power_util_info *power,
                                uint32_t *pwr);
int mic_get_vddq_power_sensor_sts(struct mic_power_util_info *power,
                                  uint32_t *sts);
int mic_get_vddq_current_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vddq_current_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_get_vddq_voltage_readings(struct mic_power_util_info *power,
                                  uint32_t *pwr);
int mic_get_vddq_voltage_sensor_sts(struct mic_power_util_info *power,
                                    uint32_t *sts);
int mic_free_power_utilization_info(struct mic_power_util_info *power);

/* power limits */
int mic_get_power_limit(struct mic_device *mdh, struct mic_power_limit **limit);
int mic_get_power_phys_limit(struct mic_power_limit *limit, uint32_t *phys_lim);
int mic_get_power_hmrk(struct mic_power_limit *limit, uint32_t *hmrk);
int mic_get_power_lmrk(struct mic_power_limit *limit, uint32_t *lmrk);
int mic_free_power_limit(struct mic_power_limit *limit);

/* memory utilization */
int mic_get_memory_utilization_info(struct mic_device *mdh,
                                    struct mic_memory_util_info **memory);
int mic_get_total_memory_size(struct mic_memory_util_info *memory,
                              uint32_t *total_size);
int mic_get_available_memory_size(struct mic_memory_util_info *memory,
                                  uint32_t *avail_size);
int mic_get_memory_buffers_size(struct mic_memory_util_info *memory,
                                uint32_t *bufs);
int mic_free_memory_utilization_info(struct mic_memory_util_info *memory);

/* core utilization apis */
int mic_alloc_core_util(struct mic_core_util **cutil);

int mic_update_core_util(struct mic_device *mdh, struct mic_core_util *cutil);

int mic_get_idle_counters(struct mic_core_util *cutil, uint64_t *idle_counters);

int mic_get_nice_counters(struct mic_core_util *cutil, uint64_t *nice_counters);

int mic_get_sys_counters(struct mic_core_util *cutil, uint64_t *sys_counters);

int mic_get_user_counters(struct mic_core_util *cutil, uint64_t *user_counters);

int mic_get_idle_sum(struct mic_core_util *cutil, uint64_t *idle_sum);

int mic_get_sys_sum(struct mic_core_util *cutil, uint64_t *sys_sum);

int mic_get_nice_sum(struct mic_core_util *cutil, uint64_t *nice_sum);

int mic_get_user_sum(struct mic_core_util *cutil, uint64_t *user_sum);

int mic_get_jiffy_counter(struct mic_core_util *cutil, uint64_t *jiffy);

int mic_get_num_cores(struct mic_core_util *cutil, uint16_t *num_cores);

int mic_get_threads_core(struct mic_core_util *cutil, uint16_t *threads_core);

int mic_free_core_util(struct mic_core_util *cutil);

/*led mode apis*/

int mic_get_led_alert(struct mic_device *mdh, uint32_t *led_alert);

int mic_set_led_alert(struct mic_device *mdh,uint32_t	*led_alert);

/*turbo apis*/

int mic_get_turbo_state_info(struct mic_device *mdh,
                             struct mic_turbo_info **turbo);

int mic_get_turbo_state(struct mic_turbo_info *turbo, uint32_t *state);

int mic_get_turbo_mode(struct mic_turbo_info *turbo, uint32_t *mode);

int mic_get_turbo_state_valid(struct mic_turbo_info *turbo, uint32_t *valid);

int mic_set_turbo_mode(struct mic_device *mdh, uint32_t *mode);

int mic_free_turbo_info(struct mic_turbo_info *turbo);

/* throttle state info */
int mic_get_throttle_state_info(struct mic_device *mdh,
                                struct mic_throttle_state_info **ttl_state);
int mic_get_thermal_ttl_active(struct mic_throttle_state_info *ttl_state,
                               int *active);
int mic_get_thermal_ttl_current_len(struct mic_throttle_state_info *ttl_state,
                                    uint32_t *current);
int mic_get_thermal_ttl_count(struct mic_throttle_state_info *ttl_state,
                              uint32_t *count);
int mic_get_thermal_ttl_time(struct mic_throttle_state_info *ttl_state,
                             uint32_t *time);
int mic_get_power_ttl_active(struct mic_throttle_state_info *ttl_state,
                             int *active);
int mic_get_power_ttl_current_len(struct mic_throttle_state_info *ttl_state,
                                  uint32_t *current);
int mic_get_power_ttl_count(struct mic_throttle_state_info *ttl_state,
                            uint32_t *count);
int mic_get_power_ttl_time(struct mic_throttle_state_info *ttl_state,
                           uint32_t *time);
int mic_free_throttle_state_info(struct mic_throttle_state_info *ttl_state);

/* device properties */
int mic_get_sysfs_attribute(struct mic_device *mdh, const char *entry,
                            char *value,
                            size_t *size);

int mic_is_ras_avail(struct mic_device *mdh, int *ras_avail);

/* uos power management config */
int mic_get_uos_pm_config(struct mic_device *mdh,
                          struct mic_uos_pm_config **pm_config);
int mic_get_cpufreq_mode(struct mic_uos_pm_config *pm_config, int *mode);
int mic_get_corec6_mode(struct mic_uos_pm_config *pm_config, int *mode);
int mic_get_pc3_mode(struct mic_uos_pm_config *pm_config, int *mode);
int mic_get_pc6_mode(struct mic_uos_pm_config *pm_config, int *mode);
int mic_free_uos_pm_config(struct mic_uos_pm_config *pm_config);
/*uuid*/
int mic_get_uuid(struct mic_device *mdh, uint8_t *uuid, size_t *size);


#endif /* MICLIB_INCLUDE_MICLIB_H_ */
