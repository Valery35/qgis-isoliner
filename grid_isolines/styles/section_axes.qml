<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="4.0.3-Norrköping" styleCategories="Symbology|Labeling">
  <renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0" referencescale="-1">
    <symbols>
      <symbol type="line" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="line_color" type="QString" value="150,150,150,200"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.2"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="use_custom_dash" type="QString" value="1"/>
            <Option name="customdash" type="QString" value="3;2"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="capstyle" type="QString" value="flat"/>
            <Option name="joinstyle" type="QString" value="bevel"/>
            <Option name="offset" type="QString" value="0"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="Sans Serif" fontSize="7" fieldName="label" isExpression="0" textColor="80,80,80,255" namedStyle="Regular">
        <text-buffer bufferDraw="1" bufferSize="0.8" bufferSizeUnits="MM" bufferColor="255,255,255,230"/>
      </text-style>
      <placement placement="1" placementFlags="0" dist="0" distUnits="MM" repeatDistance="0"/>
      <rendering obstacle="0" upsidedownLabels="0" mergeLines="1" labelPerPart="0"/>
      <dd_properties>
        <Option type="Map">
          <Option name="name" type="QString" value=""/>
          <Option name="properties" type="Map">
            <Option name="Hali" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="'Right'"/>
              <Option name="type" type="int" value="3"/>
            </Option>
            <Option name="PositionX" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="x_min($geometry) - (x_max($geometry)-x_min($geometry))*0.03"/>
              <Option name="type" type="int" value="3"/>
            </Option>
            <Option name="PositionY" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="(y_min($geometry)+y_max($geometry))/2"/>
              <Option name="type" type="int" value="3"/>
            </Option>
            <Option name="Vali" type="Map">
              <Option name="active" type="bool" value="true"/>
              <Option name="expression" type="QString" value="'Half'"/>
              <Option name="type" type="int" value="3"/>
            </Option>
          </Option>
          <Option name="type" type="QString" value="collection"/>
        </Option>
      </dd_properties>
    </settings>
  </labeling>
  <blendMode>0</blendMode>
</qgis>
