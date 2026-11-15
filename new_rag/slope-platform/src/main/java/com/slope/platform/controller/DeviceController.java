package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.dto.DeviceRequest;
import com.slope.platform.entity.Device;
import com.slope.platform.service.DeviceService;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 设备台账接口。支持按 site_id / status 过滤（运维最常用的两个筛选维度）。
 */
@RestController
@RequestMapping("/api/devices")
public class DeviceController {

    private final DeviceService deviceService;

    public DeviceController(DeviceService deviceService) {
        this.deviceService = deviceService;
    }

    @GetMapping
    public ApiResponse<List<Device>> list(
            @RequestParam(name = "site_id", required = false) Integer siteId,
            @RequestParam(name = "status", required = false) String status) {
        return ApiResponse.ok(deviceService.list(siteId, status));
    }

    @GetMapping("/{id}")
    public ApiResponse<Device> get(@PathVariable Integer id) {
        return ApiResponse.ok(deviceService.get(id));
    }

    @PostMapping
    public ApiResponse<Device> create(@Valid @RequestBody DeviceRequest request) {
        return ApiResponse.ok(deviceService.create(request));
    }

    @PutMapping("/{id}")
    public ApiResponse<Device> update(@PathVariable Integer id, @Valid @RequestBody DeviceRequest request) {
        return ApiResponse.ok(deviceService.update(id, request));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable Integer id) {
        deviceService.delete(id);
        return ApiResponse.ok();
    }
}
