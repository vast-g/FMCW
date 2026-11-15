package com.slope.platform.service;

import com.slope.platform.common.BusinessException;
import com.slope.platform.dto.DeviceRequest;
import com.slope.platform.entity.Device;
import com.slope.platform.entity.enums.DeviceStatus;
import com.slope.platform.entity.enums.DeviceType;
import com.slope.platform.repository.DeviceRepository;
import com.slope.platform.repository.SiteRepository;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 设备台账业务。
 */
@Service
@Transactional(readOnly = true)
public class DeviceService {

    private final DeviceRepository deviceRepository;
    private final SiteRepository siteRepository;

    public DeviceService(DeviceRepository deviceRepository, SiteRepository siteRepository) {
        this.deviceRepository = deviceRepository;
        this.siteRepository = siteRepository;
    }

    public List<Device> list(Integer siteId, String status) {
        // status 先过枚举解析：非法值在入口就报 400 并列出可选值，不会带着脏数据去查库；
        // 再用 code 去匹配，这样调用方传 ONLINE / online 都能命中库里的小写码。
        String statusCode = (status == null || status.isBlank())
                ? null
                : DeviceStatus.fromCode(status).getCode();
        return deviceRepository.search(siteId, statusCode);
    }

    public Device get(Integer id) {
        return deviceRepository.findById(id)
                .orElseThrow(() -> BusinessException.notFound("设备不存在: id=" + id));
    }

    @Transactional
    public Device create(DeviceRequest request) {
        requireSite(request.siteId());
        if (deviceRepository.existsByCode(request.code())) {
            throw BusinessException.conflict("设备编号已存在: " + request.code());
        }
        Device device = new Device();
        apply(device, request);
        return deviceRepository.save(device);
    }

    @Transactional
    public Device update(Integer id, DeviceRequest request) {
        Device device = get(id);
        requireSite(request.siteId());
        if (deviceRepository.existsByCodeAndIdNot(request.code(), id)) {
            throw BusinessException.conflict("设备编号已被其它设备占用: " + request.code());
        }
        apply(device, request);
        return deviceRepository.save(device);
    }

    @Transactional
    public void delete(Integer id) {
        deviceRepository.delete(get(id));
    }

    /**
     * 校验点位存在。
     *
     * <p>依赖数据库外键也会报错，但错误信息是 "Cannot add or update a child row"，
     * 对使用者没有任何帮助。这里提前查一次，换成人能看懂的提示。
     */
    private void requireSite(Integer siteId) {
        if (!siteRepository.existsById(siteId)) {
            throw BusinessException.badRequest("所属点位不存在: site_id=" + siteId);
        }
    }

    private void apply(Device device, DeviceRequest request) {
        device.setCode(request.code().trim());
        device.setName(request.name().trim());
        device.setSiteId(request.siteId());
        device.setType(DeviceType.fromCode(request.type()).getCode());
        device.setVendor(request.vendor());
        device.setModel(request.model());
        // 未指定状态时保持"离线"语义，不做自动推断（在线与否应由心跳决定，不能靠人工填写时猜）
        if (request.status() != null && !request.status().isBlank()) {
            device.setStatus(DeviceStatus.fromCode(request.status()).getCode());
        }
        device.setLastHeartbeat(request.lastHeartbeat());
        device.setInstalledAt(request.installedAt());
    }
}
