<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="4.0.3-Norrköping" styleCategories="Symbology|Labeling">
  <renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0" referencescale="-1">
    <symbols>
      <symbol type="line" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="line_color" type="QString" value="170,30,30,255"/>
            <Option name="line_width" type="QString" value="0.4"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="line_style" type="QString" value="dash"/>
            <Option name="customdash" type="QString" value="4;2"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="use_custom_dash" type="QString" value="1"/>
            <Option name="capstyle" type="QString" value="flat"/>
            <Option name="joinstyle" type="QString" value="bevel"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="Sans Serif" fontSize="8" fieldName="label" isExpression="0" textColor="140,20,20,255" namedStyle="Bold"/>
      <placement placement="2" lineAnchorPercent="0.04" lineAnchorClipping="0" dist="1" rotationAngle="0"/>
      <rendering obstacle="0" upsidedownLabels="0" labelPerPart="0"/>
    </settings>
  </labeling>
  <blendMode>0</blendMode>
</qgis>
