<script lang="ts">
  import { DropdownMenu as DropdownMenuPrimitive } from 'bits-ui';

  type Props = DropdownMenuPrimitive.GroupProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<DropdownMenuPrimitive.Group {...restProps}>
  {@render children?.()}
</DropdownMenuPrimitive.Group>
